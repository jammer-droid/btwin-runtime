from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from contextlib import suppress
from typing import Any

from btwin_core.prototypes.persistent_sessions.base import PersistentSessionAdapter
from btwin_core.prototypes.persistent_sessions.types import (
    build_runtime_debug_session_metadata,
    SessionCloseResult,
    SessionConfig,
    SessionEvent,
    SessionHealth,
    SessionStartResult,
    SessionTurn,
)

_STREAM_END = object()
_RequestId = int | str
_RequestKey = str


class CodexAppServerPersistentAdapter(PersistentSessionAdapter):
    provider = "codex-app-server"
    capability = "live_persistent"
    continuity_mode = "thread_rpc_session"
    launch_strategy = "single_process_app_server"

    def __init__(
        self,
        *,
        command: str = "codex",
        start_timeout: float = 10.0,
        event_timeout: float = 0.05,
        close_timeout: float = 0.2,
    ) -> None:
        self._command = command
        self._start_timeout = start_timeout
        self._event_timeout = event_timeout
        self._close_timeout = close_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._event_queue: asyncio.Queue[SessionEvent] = asyncio.Queue()
        self._prefetched_events: deque[SessionEvent] = deque()
        self._pending_responses: dict[_RequestKey, asyncio.Future[dict[str, Any]]] = {}
        self._buffered_responses: dict[_RequestKey, dict[str, Any]] = {}
        self._next_request_id = 1
        self._pid: int | None = None
        self._session_id: str | None = None
        self._current_turn_index: int | None = None
        self._requested_model: str | None = None
        self._requested_effort: str | None = None
        self._effective_model: str | None = None
        self._effective_effort: str | None = None
        self._last_command: list[str] = []
        self._stderr_lines: list[str] = []
        self._closed = False

    async def start(self, config: SessionConfig) -> SessionStartResult:
        if self._process is not None:
            return self._failure_start_result(
                message="adapter is already started",
                command=self._last_command,
                error_kind="already_started",
            )

        self._reset_runtime_state()
        self._closed = False
        self._requested_model = self._resolve_requested_value(config, "model")
        self._requested_effort = self._resolve_requested_value(config, "effort")
        command = self._build_launch_command(config)
        self._last_command = command

        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_launch_env(config),
            )
        except FileNotFoundError as exc:
            return self._failure_start_result(
                message=str(exc),
                command=command,
                error_kind="not_found",
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure_start_result(
                message=str(exc),
                command=command,
                error_kind="launch_failed",
            )

        self._pid = self._process.pid
        self._reader_task = asyncio.create_task(self._pump_stdout())
        self._stderr_task = asyncio.create_task(self._pump_stderr())

        try:
            await self._send_request(
                "initialize",
                {"clientInfo": {"name": "btwin-prototype", "version": "0.1.0"}, "capabilities": {}},
            )
            await self._send_notification("initialized", {})
            thread_response = await self._send_request(
                "thread/resume" if self._resolve_resume_session_id(config) else "thread/start",
                self._build_thread_params(config),
            )
        except Exception as exc:  # noqa: BLE001
            await self._shutdown_process()
            return self._failure_start_result(
                message=str(exc),
                command=command,
                error_kind="startup_rpc_failed",
            )

        thread = thread_response.get("thread")
        if isinstance(thread, dict):
            thread_id = thread.get("id")
            if isinstance(thread_id, str) and thread_id:
                self._session_id = thread_id
        self._ingest_effective_metadata(thread_response)

        return SessionStartResult(
            session_id=self._session_id or "",
            events=(),
            metadata={
                "provider": self.provider,
                "ok": True,
                "command": command,
                **self._runtime_debug_metadata(),
                "capability": self.capability,
                "continuity_mode": self.continuity_mode,
                "launch_strategy": self.launch_strategy,
                "stderr_lines": list(self._stderr_lines),
            },
        )

    async def send_turn(self, turn: SessionTurn) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise RuntimeError("codex app-server does not expose stdin")
        if not self._session_id:
            raise RuntimeError("codex app-server thread is not started")

        turn_index = turn.metadata.get("turn_index")
        self._current_turn_index = turn_index if isinstance(turn_index, int) else None

        params: dict[str, Any] = {
            "threadId": self._session_id,
            "input": [{"type": "text", "text": turn.content}],
        }
        if self._requested_model:
            params["model"] = self._requested_model
        if self._requested_effort:
            params["effort"] = self._requested_effort

        response = await self._send_request("turn/start", params)
        self._ingest_effective_metadata(response)
        response_turn = response.get("turn")
        response_turn_id = response_turn.get("id") if isinstance(response_turn, dict) else None
        if isinstance(response_turn_id, str) and response_turn_id:
            self._prefetched_events.append(
                SessionEvent(
                    kind="turn_started",
                    content=response_turn_id,
                    metadata={
                        **self._runtime_debug_metadata(),
                        "provider": self.provider,
                        "raw": {
                            "method": "turn/start",
                            "result": response,
                        },
                        "source": "turn_start_response",
                    },
                )
            )

    def read_events(self):
        async def iterator():
            while True:
                if self._prefetched_events:
                    yield self._prefetched_events.popleft()
                    continue
                event = await self._event_queue.get()
                if event is _STREAM_END:
                    break
                yield event

        return iterator()

    async def health_check(self) -> SessionHealth:
        if self._closed:
            return SessionHealth(
                ok=False,
                message="closed",
                metadata={
                    "provider": self.provider,
                    "state": "closed",
                    **self._runtime_debug_metadata(),
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                    "launch_strategy": self.launch_strategy,
                },
            )
        process = self._process
        if process is None:
            return SessionHealth(
                ok=False,
                message="not started",
                metadata={
                    "provider": self.provider,
                    "state": "not_started",
                    **self._runtime_debug_metadata(),
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                    "launch_strategy": self.launch_strategy,
                },
            )

        if process.returncode is None:
            return SessionHealth(
                ok=True,
                message=None,
                metadata={
                    "provider": self.provider,
                    "state": "running",
                    **self._runtime_debug_metadata(),
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                    "launch_strategy": self.launch_strategy,
                },
            )

        return SessionHealth(
            ok=False,
            message=f"process exited with code {process.returncode}",
            metadata={
                "provider": self.provider,
                "state": "exited",
                "returncode": process.returncode,
                **self._runtime_debug_metadata(),
                "capability": self.capability,
                "continuity_mode": self.continuity_mode,
                "launch_strategy": self.launch_strategy,
            },
        )

    async def close(self) -> SessionCloseResult:
        return await self._shutdown_process()

    async def _pump_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        try:
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    await self._event_queue.put(
                        SessionEvent(
                            kind="unsupported",
                            content=line,
                            metadata={
                                **self._runtime_debug_metadata(),
                                "provider": self.provider,
                                "reason": "malformed_json",
                            },
                        )
                    )
                    continue

                response_key = self._request_id_key(payload.get("id"))
                if response_key is not None:
                    future = self._pending_responses.pop(response_key, None)
                    if future is None:
                        self._buffered_responses[response_key] = payload
                    elif not future.done():
                        if "error" in payload:
                            future.set_exception(RuntimeError(json.dumps(payload["error"])))
                        else:
                            future.set_result(payload.get("result", {}))
                    continue

                event = self._parse_notification(payload)
                if event is not None:
                    await self._event_queue.put(event)
        finally:
            for future in self._pending_responses.values():
                if not future.done():
                    future.set_exception(RuntimeError("codex app-server closed"))
            self._pending_responses.clear()
            await self._event_queue.put(_STREAM_END)

    async def _pump_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        async for raw_line in process.stderr:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if line:
                self._stderr_lines.append(line)

    def _parse_notification(self, payload: dict[str, Any]) -> SessionEvent | None:
        method = payload.get("method")
        params = payload.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return None

        self._ingest_effective_metadata(params)

        metadata = {
            **self._runtime_debug_metadata(),
            "provider": self.provider,
            "raw": payload,
        }

        if method == "thread/started":
            thread = params.get("thread")
            if isinstance(thread, dict):
                thread_id = thread.get("id")
                if isinstance(thread_id, str) and thread_id:
                    self._session_id = thread_id
                    metadata["session_id"] = thread_id
            return SessionEvent(kind="session_started", content=self._session_id, metadata=metadata)

        if method == "turn/started":
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            return SessionEvent(
                kind="turn_started",
                content=turn_id if isinstance(turn_id, str) else None,
                metadata=metadata,
            )

        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            return SessionEvent(
                kind="text_delta",
                content=delta if isinstance(delta, str) else None,
                metadata=metadata,
            )

        if method == "turn/completed":
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            return SessionEvent(
                kind="turn_complete",
                content=turn_id if isinstance(turn_id, str) else None,
                metadata=metadata,
            )

        return SessionEvent(kind="unsupported", content=method, metadata=metadata)

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        process = self._require_process()
        if process.stdin is None:
            raise RuntimeError("codex app-server stdin is unavailable")

        request_id = self._next_request_id_value()
        request_key = self._request_id_key(request_id)
        assert request_key is not None
        buffered = self._buffered_responses.pop(request_key, None)
        if buffered is not None:
            if "error" in buffered:
                raise RuntimeError(json.dumps(buffered["error"]))
            return buffered.get("result", {})
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_responses[request_key] = future

        process.stdin.write(
            (
                json.dumps(
                    {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
                )
                + "\n"
            ).encode("utf-8")
        )
        drained = process.stdin.drain()
        if asyncio.iscoroutine(drained):
            await drained

        return await asyncio.wait_for(future, timeout=self._start_timeout)

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise RuntimeError("codex app-server stdin is unavailable")
        process.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode("utf-8")
        )
        drained = process.stdin.drain()
        if asyncio.iscoroutine(drained):
            await drained

    def _build_thread_params(self, config: SessionConfig) -> dict[str, Any]:
        params: dict[str, Any] = {}
        resume_session_id = self._resolve_resume_session_id(config)
        if resume_session_id:
            params["threadId"] = resume_session_id
        if self._requested_model:
            params["model"] = self._requested_model
        if self._requested_effort:
            params["config"] = {"model_reasoning_effort": self._requested_effort}
        return params

    def _build_launch_command(self, config: SessionConfig) -> list[str]:
        command = [
            self._command,
            "-a",
            "never",
            "-s",
            "danger-full-access",
            "app-server",
            "--listen",
            "stdio://",
        ]
        extra_args = config.options.get("args")
        if isinstance(extra_args, (list, tuple)):
            command.extend(str(arg) for arg in extra_args)
        return command

    def _build_launch_env(self, config: SessionConfig) -> dict[str, str]:
        env = os.environ.copy()
        extra_env = config.options.get("env")
        if isinstance(extra_env, dict):
            env.update({str(key): str(value) for key, value in extra_env.items()})
        return env

    def _resolve_resume_session_id(self, config: SessionConfig) -> str | None:
        candidate = (
            config.options.get("resume_session_id")
            or config.metadata.get("resume_session_id")
            or config.metadata.get("session_id")
        )
        if candidate is None:
            return None
        text = str(candidate).strip()
        return text or None

    def _next_request_id_value(self) -> _RequestId:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _request_id_key(self, request_id: object) -> _RequestKey | None:
        if isinstance(request_id, bool):
            return None
        if isinstance(request_id, (int, str)):
            return str(request_id)
        return None

    def _resolve_requested_value(self, config: SessionConfig, key: str) -> str | None:
        candidate = config.metadata.get(f"requested_{key}")
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        candidate = config.options.get(f"requested_{key}")
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        candidate = config.metadata.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        candidate = config.options.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        return None

    def _extract_string(self, mapping: dict[str, Any], key: str) -> str | None:
        candidate = mapping.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        return None

    def _runtime_debug_metadata(self) -> dict[str, Any]:
        return build_runtime_debug_session_metadata(
            provider=self.provider,
            pid=self._pid,
            session_id=self._session_id,
            turn=self._current_turn_index,
            requested_model=self._requested_model,
            requested_effort=self._requested_effort,
            effective_model=self._effective_model,
            effective_effort=self._effective_effort,
        )

    def _require_process(self) -> asyncio.subprocess.Process:
        process = self._process
        if process is None:
            raise RuntimeError("codex app-server has not been started")
        return process

    async def _shutdown_process(self) -> SessionCloseResult:
        process = self._process
        if process is None:
            return SessionCloseResult(
                ok=False,
                message="not started",
                metadata={
                    "provider": self.provider,
                    "state": "not_started",
                    **self._runtime_debug_metadata(),
                    "command": list(self._last_command),
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                    "launch_strategy": self.launch_strategy,
                },
            )

        ok = True
        message: str | None = None
        metadata = {
            "provider": self.provider,
            **self._runtime_debug_metadata(),
            "command": list(self._last_command),
            "stderr_lines": list(self._stderr_lines),
            "capability": self.capability,
            "continuity_mode": self.continuity_mode,
            "launch_strategy": self.launch_strategy,
        }

        if process.stdin is not None:
            try:
                process.stdin.close()
            except Exception as exc:  # noqa: BLE001
                ok = False
                message = message or str(exc)
                metadata["stdin_close_error"] = str(exc)

        try:
            process.terminate()
        except Exception as exc:  # noqa: BLE001
            ok = False
            message = message or str(exc)
            metadata["terminate_error"] = str(exc)

        try:
            await asyncio.wait_for(process.wait(), timeout=self._close_timeout)
        except Exception as exc:  # noqa: BLE001
            ok = False
            message = message or str(exc)
            metadata["wait_error"] = str(exc)

        if process.returncode is None:
            try:
                process.kill()
            except Exception as exc:  # noqa: BLE001
                ok = False
                message = message or str(exc)
                metadata["kill_error"] = str(exc)
            try:
                await process.wait()
            except Exception as exc:  # noqa: BLE001
                ok = False
                message = message or str(exc)
                metadata["kill_wait_error"] = str(exc)

        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stderr_task

        self._process = None
        self._closed = True
        metadata["error_kind"] = "closed" if ok else "shutdown_failed"
        return SessionCloseResult(ok=ok, message=message, metadata=metadata)

    def _ingest_effective_metadata(self, payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return

        if self._effective_model is None:
            effective_model = self._extract_string(payload, "effective_model")
            if effective_model is not None:
                self._effective_model = effective_model

        if self._effective_effort is None:
            effective_effort = self._extract_string(payload, "effective_effort")
            if effective_effort is not None:
                self._effective_effort = effective_effort

    def _failure_start_result(
        self,
        *,
        message: str,
        command: list[str],
        error_kind: str,
    ) -> SessionStartResult:
        return SessionStartResult(
            session_id="",
            events=(),
            metadata={
                "provider": self.provider,
                "ok": False,
                "message": message,
                "error_kind": error_kind,
                "command": command,
                **self._runtime_debug_metadata(),
                "capability": self.capability,
                "continuity_mode": self.continuity_mode,
                "launch_strategy": self.launch_strategy,
                "stderr_lines": list(self._stderr_lines),
            },
        )

    def _reset_runtime_state(self) -> None:
        self._process = None
        self._reader_task = None
        self._stderr_task = None
        self._event_queue = asyncio.Queue()
        self._pending_responses = {}
        self._buffered_responses = {}
        self._next_request_id = 1
        self._pid = None
        self._session_id = None
        self._current_turn_index = None
        self._effective_model = None
        self._effective_effort = None
        self._stderr_lines = []
        self._closed = False
