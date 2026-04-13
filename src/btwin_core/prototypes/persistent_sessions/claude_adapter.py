from __future__ import annotations

import asyncio
import json
import os
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


class ClaudePersistentAdapter(PersistentSessionAdapter):
    provider = "claude"
    capability = "live_persistent"
    continuity_mode = "same_process_stdin_stdout"
    launch_strategy = "single_process"

    def __init__(
        self,
        *,
        command: str = "claude",
        start_timeout: float = 0.2,
        event_timeout: float = 0.05,
        close_timeout: float = 0.2,
    ) -> None:
        self._command = command
        self._start_timeout = start_timeout
        self._event_timeout = event_timeout
        self._close_timeout = close_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._event_queue: asyncio.Queue[SessionEvent] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[int] | None = None
        self._session_ready = asyncio.Event()
        self._startup_evidence = asyncio.Event()
        self._startup_confirmed = asyncio.Event()
        self._session_id: str | None = None
        self._pid: int | None = None
        self._requested_model: str | None = None
        self._requested_effort: str | None = None
        self._effective_model: str | None = None
        self._effective_effort: str | None = None
        self._current_turn_index: int | None = None
        self._last_command: list[str] = []
        self._stderr_lines: list[str] = []

    async def start(self, config: SessionConfig) -> SessionStartResult:
        if self._process is not None:
            return self._failure_start_result(
                message="adapter is already started",
                command=self._last_command,
                error_kind="already_started",
            )

        self._reset_runtime_state()
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
        except Exception as exc:  # noqa: BLE001 - prototype keeps launch errors explicit
            return self._failure_start_result(
                message=str(exc),
                command=command,
                error_kind="launch_failed",
            )

        self._reader_task = asyncio.create_task(self._pump_stdout())
        self._stderr_task = asyncio.create_task(self._pump_stderr())
        self._wait_task = asyncio.create_task(self._process.wait())
        self._pid = self._process.pid

        startup_state = await self._wait_for_session_ready()
        if startup_state[0] != "ready":
            state, returncode, detail = startup_state
            if state == "exited_before_init":
                shutdown_result = await self._shutdown_process()
                return self._failure_start_result(
                    message="process exited before init",
                    command=command,
                    error_kind="process_exited_before_init",
                    returncode=returncode,
                    detail=detail,
                    shutdown_result=shutdown_result,
                )
            shutdown_result = await self._shutdown_process()
            return self._failure_start_result(
                message="session start timed out",
                command=command,
                error_kind="start_timeout",
                shutdown_result=shutdown_result,
            )

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
            raise RuntimeError("claude process does not expose stdin")
        turn_index = turn.metadata.get("turn_index")
        self._current_turn_index = turn_index if isinstance(turn_index, int) else None

        payload = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": turn.content}],
            },
        }
        process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        drain = getattr(process.stdin, "drain", None)
        if drain is not None:
            result = drain()
            if asyncio.iscoroutine(result):
                await result

    def read_events(self):
        async def iterator():
            while True:
                event = await self._next_event()
                if event is None:
                    break
                yield event

        return iterator()

    async def health_check(self) -> SessionHealth:
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

        returncode = getattr(process, "returncode", None)
        if returncode is None:
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
            message=f"process exited with code {returncode}",
            metadata={
                "provider": self.provider,
                "state": "exited",
                "returncode": returncode,
                **self._runtime_debug_metadata(),
                "capability": self.capability,
                "continuity_mode": self.continuity_mode,
                "launch_strategy": self.launch_strategy,
            },
        )

    async def close(self) -> SessionCloseResult:
        return await self._shutdown_process()

    def _build_launch_command(self, config: SessionConfig) -> list[str]:
        command = [
            self._command,
            "--print",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
        ]
        model = self._resolve_requested_value(config, "model")
        effort = self._resolve_requested_value(config, "effort")
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["--effort", effort])
        command.extend(["--verbose", "--include-partial-messages"])
        resume_session_id = (
            config.options.get("resume_session_id")
            or config.metadata.get("resume_session_id")
            or config.metadata.get("session_id")
        )
        if resume_session_id:
            command += ["--resume", str(resume_session_id)]
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

    def _failure_start_result(
        self,
        *,
        message: str,
        command: list[str],
        error_kind: str,
        returncode: int | None = None,
        detail: str | None = None,
        shutdown_result: SessionCloseResult | None = None,
    ) -> SessionStartResult:
        metadata: dict[str, Any] = {
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
        }
        if returncode is not None:
            metadata["returncode"] = returncode
        if detail is not None:
            metadata["detail"] = detail
        if shutdown_result is not None:
            metadata["shutdown_ok"] = shutdown_result.ok
            if shutdown_result.message is not None:
                metadata["shutdown_message"] = shutdown_result.message
            shutdown_metadata = dict(shutdown_result.metadata)
            metadata["shutdown_metadata"] = shutdown_metadata
            shutdown_error_kind = shutdown_metadata.get("error_kind")
            if isinstance(shutdown_error_kind, str) and shutdown_error_kind:
                metadata["shutdown_error_kind"] = shutdown_error_kind
            shutdown_state = shutdown_metadata.get("state")
            if isinstance(shutdown_state, str) and shutdown_state:
                metadata["shutdown_state"] = shutdown_state
        return SessionStartResult(session_id="", events=(), metadata=metadata)

    def _reset_runtime_state(self) -> None:
        self._event_queue = asyncio.Queue()
        self._reader_task = None
        self._stderr_task = None
        self._wait_task = None
        self._session_ready = asyncio.Event()
        self._startup_evidence = asyncio.Event()
        self._startup_confirmed = asyncio.Event()
        self._session_id = None
        self._pid = None
        self._requested_model = None
        self._requested_effort = None
        self._effective_model = None
        self._effective_effort = None
        self._current_turn_index = None
        self._process = None
        self._stderr_lines = []

    def _require_process(self) -> asyncio.subprocess.Process:
        process = self._process
        if process is None:
            raise RuntimeError("claude process has not been started")
        return process

    async def _wait_for_session_ready(self) -> tuple[str, int | None, str | None]:
        ready_task = asyncio.create_task(self._session_ready.wait())
        evidence_task = asyncio.create_task(self._startup_evidence.wait())
        wait_task = self._wait_task
        if wait_task is None:
            ready_task.cancel()
            evidence_task.cancel()
            with suppress(asyncio.CancelledError):
                await ready_task
            with suppress(asyncio.CancelledError):
                await evidence_task
            return ("timeout", None, "missing_wait_task")

        done: set[asyncio.Task[Any]]
        pending: set[asyncio.Task[Any]]
        try:
            done, pending = await asyncio.wait(
                {ready_task, evidence_task, wait_task},
                timeout=self._start_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except Exception:
            ready_task.cancel()
            evidence_task.cancel()
            with suppress(asyncio.CancelledError):
                await ready_task
            with suppress(asyncio.CancelledError):
                await evidence_task
            raise

        if ready_task in done:
            evidence_task.cancel()
            with suppress(asyncio.CancelledError):
                await evidence_task
            return ("ready", None, None)

        if wait_task in done:
            ready_task.cancel()
            evidence_task.cancel()
            with suppress(asyncio.CancelledError):
                await ready_task
            with suppress(asyncio.CancelledError):
                await evidence_task
            try:
                returncode = wait_task.result()
            except Exception as exc:  # noqa: BLE001 - explicit prototype startup reporting
                return ("exited_before_init", None, str(exc))
            return ("exited_before_init", returncode, None)

        if evidence_task in done:
            grace_timeout = min(self._event_timeout, self._start_timeout)
            done, _ = await asyncio.wait(
                {ready_task, wait_task},
                timeout=grace_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            ready_task.cancel()
            with suppress(asyncio.CancelledError):
                await ready_task
            if ready_task in done:
                return ("ready", None, None)
            if wait_task in done or getattr(self._process, "returncode", None) is not None:
                try:
                    returncode = await wait_task
                except Exception as exc:  # noqa: BLE001 - explicit prototype startup reporting
                    return ("exited_before_init", None, str(exc))
                return ("exited_before_init", returncode, None)
            return ("ready", None, None)

        if wait_task.done():
            try:
                returncode = wait_task.result()
            except Exception as exc:  # noqa: BLE001 - explicit prototype startup reporting
                return ("exited_before_init", None, str(exc))
            return ("exited_before_init", returncode, None)

        ready_task.cancel()
        evidence_task.cancel()
        with suppress(asyncio.CancelledError):
            await ready_task
        with suppress(asyncio.CancelledError):
            await evidence_task
        return ("timeout", None, None)

    async def _pump_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        try:
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                event = self.parse_event_line(line)
                if event is None:
                    continue
                await self._event_queue.put(event)
                if self._startup_confirmed.is_set() and not self._session_ready.is_set():
                    self._session_ready.set()
        finally:
            await self._event_queue.put(_STREAM_END)

    async def _pump_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return

        async for raw_line in process.stderr:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if line:
                self._stderr_lines.append(line)

    async def _next_event(self) -> SessionEvent | None:
        event = await self._event_queue.get()
        if event is _STREAM_END:
            return None
        return event

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
                    "command": self._last_command,
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                    "launch_strategy": self.launch_strategy,
                },
            )

        ok = True
        failure_message: str | None = None
        metadata: dict[str, Any] = {
            "provider": self.provider,
            **self._runtime_debug_metadata(),
            "command": self._last_command,
            "capability": self.capability,
            "continuity_mode": self.continuity_mode,
            "launch_strategy": self.launch_strategy,
            "stderr_lines": list(self._stderr_lines),
            "state": "closed",
        }

        if process.stdin is not None:
            try:
                process.stdin.close()
            except Exception as exc:  # noqa: BLE001 - explicit prototype shutdown reporting
                ok = False
                failure_message = failure_message or str(exc)
                metadata["stdin_close_error"] = str(exc)

        try:
            process.terminate()
        except Exception as exc:  # noqa: BLE001 - explicit prototype shutdown reporting
            ok = False
            failure_message = failure_message or str(exc)
            metadata["terminate_error"] = str(exc)

        try:
            await asyncio.wait_for(process.wait(), timeout=self._close_timeout)
        except Exception as exc:  # noqa: BLE001 - explicit prototype shutdown reporting
            ok = False
            failure_message = failure_message or str(exc)
            metadata["wait_error"] = str(exc)

        if getattr(process, "returncode", None) is None:
            try:
                process.kill()
            except Exception as exc:  # noqa: BLE001 - explicit prototype shutdown reporting
                ok = False
                failure_message = failure_message or str(exc)
                metadata["kill_error"] = str(exc)
            try:
                await process.wait()
            except Exception as exc:  # noqa: BLE001 - explicit prototype shutdown reporting
                ok = False
                failure_message = failure_message or str(exc)
                metadata["kill_wait_error"] = str(exc)

        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stderr_task

        await self._event_queue.put(_STREAM_END)

        self._process = None
        metadata["error_kind"] = "shutdown_failed" if not ok else "closed"
        return SessionCloseResult(ok=ok, message=failure_message, metadata=metadata)

    def parse_event_line(self, line: str) -> SessionEvent | None:
        stripped = line.strip()
        if not stripped:
            return None

        try:
            data: dict[str, Any] = json.loads(stripped)
        except json.JSONDecodeError:
            return SessionEvent(
                kind="unsupported",
                content=stripped,
                metadata={
                    **build_runtime_debug_session_metadata(
                        provider=self.provider,
                        pid=self._runtime_pid(),
                        session_id=self._session_id,
                        turn=self._current_turn_index,
                        requested_model=self._requested_model,
                        requested_effort=self._requested_effort,
                        effective_model=self._effective_model,
                        effective_effort=self._effective_effort,
                    ),
                    "reason": "malformed_json",
                    "provider": self.provider,
                },
            )

        self._ingest_effective_metadata(data)
        event_type = data.get("type")
        session_id = data.get("session_id")
        pid = self._runtime_pid()
        turn_index = self._current_turn_index

        if event_type == "system" and session_id and self._session_id is None:
            self._session_id = str(session_id)
        if event_type == "system" and session_id:
            self._startup_evidence.set()

        if (event_type == "system" and data.get("subtype") == "init") or event_type == "system/init":
            session_id = str(session_id or "")
            if session_id and self._session_id is None:
                self._session_id = session_id
            self._startup_confirmed.set()
            return SessionEvent(
                kind="session_started",
                content=session_id or None,
                metadata={
                    **build_runtime_debug_session_metadata(
                        provider=self.provider,
                        pid=pid,
                        session_id=session_id or None,
                        turn=turn_index,
                        requested_model=self._requested_model,
                        requested_effort=self._requested_effort,
                        effective_model=self._effective_model,
                        effective_effort=self._effective_effort,
                    ),
                    "raw": data,
                    "provider": self.provider,
                },
            )

        if event_type == "assistant":
            content = self._extract_assistant_text(data)
            return SessionEvent(
                kind="text_delta",
                content=content,
                metadata={
                    **build_runtime_debug_session_metadata(
                        provider=self.provider,
                        pid=pid,
                        session_id=self._session_id,
                        turn=turn_index,
                        requested_model=self._requested_model,
                        requested_effort=self._requested_effort,
                        effective_model=self._effective_model,
                        effective_effort=self._effective_effort,
                    ),
                    "raw": data,
                    "provider": self.provider,
                },
            )

        if event_type == "result":
            if session_id and self._session_id is None:
                self._session_id = str(session_id)
            self._startup_confirmed.set()
            return SessionEvent(
                kind="complete",
                content=str(data.get("result", "")) or None,
                metadata={
                    **build_runtime_debug_session_metadata(
                        provider=self.provider,
                        pid=pid,
                        session_id=session_id or self._session_id,
                        turn=turn_index,
                        requested_model=self._requested_model,
                        requested_effort=self._requested_effort,
                        effective_model=self._effective_model,
                        effective_effort=self._effective_effort,
                    ),
                    "raw": data,
                    "provider": self.provider,
                },
            )

        return SessionEvent(
            kind="unsupported",
            content=stripped,
            metadata={
                **build_runtime_debug_session_metadata(
                    provider=self.provider,
                    pid=pid,
                    session_id=self._session_id,
                    turn=turn_index,
                    requested_model=self._requested_model,
                    requested_effort=self._requested_effort,
                    effective_model=self._effective_model,
                    effective_effort=self._effective_effort,
                ),
                "raw": data,
                "provider": self.provider,
            },
        )

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

    def _runtime_pid(self) -> int | None:
        if self._pid is not None:
            return self._pid
        process = self._process
        if process is None:
            return None
        pid = getattr(process, "pid", None)
        return pid if isinstance(pid, int) else None

    def _runtime_debug_metadata(
        self,
        *,
        pid: int | None | object = ...,
    ) -> dict[str, Any]:
        resolved_pid = self._runtime_pid() if pid is ... else pid
        return build_runtime_debug_session_metadata(
            provider=self.provider,
            pid=resolved_pid,
            session_id=self._session_id,
            turn=self._current_turn_index,
            requested_model=self._requested_model,
            requested_effort=self._requested_effort,
            effective_model=self._effective_model,
            effective_effort=self._effective_effort,
        )

    def _extract_assistant_text(self, data: dict[str, Any]) -> str | None:
        message = data.get("message")
        if not isinstance(message, dict):
            return None

        content = message.get("content")
        if not isinstance(content, list):
            return None

        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts) or None

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

    def _extract_string(self, payload: dict[str, Any], key: str) -> str | None:
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        return None
