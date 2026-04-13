from __future__ import annotations

import asyncio
import json
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


class CodexPersistentAdapter(PersistentSessionAdapter):
    provider = "codex"
    capability = "partial_persistent"
    continuity_mode = "resume_invocation"
    launch_strategy = "per_turn_exec"

    def __init__(
        self,
        *,
        command: str = "codex",
        turn_timeout: float = 30.0,
        close_timeout: float = 0.2,
    ) -> None:
        self._command = command
        self._turn_timeout = turn_timeout
        self._close_timeout = close_timeout
        self._started = False
        self._closed = False
        self._session_id: str | None = None
        self._extra_args: list[str] = []
        self._last_command: list[str] = []
        self._stderr_lines: list[str] = []
        self._pending_events: list[SessionEvent] = []
        self._active_process: asyncio.subprocess.Process | None = None
        self._active_pid: int | None = None
        self._requested_model: str | None = None
        self._requested_effort: str | None = None
        self._effective_model: str | None = None
        self._effective_effort: str | None = None
        self._current_turn_index: int | None = None
        self._turn_lock = asyncio.Lock()
        self._last_failure: dict[str, str] | None = None

    async def start(self, config: SessionConfig) -> SessionStartResult:
        if self._started and not self._closed:
            return SessionStartResult(
                session_id=self._session_id or "",
                events=(),
                metadata={
                    "provider": self.provider,
                    "ok": False,
                    "message": "adapter is already started",
                    "error_kind": "already_started",
                    **self._runtime_debug_metadata(),
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                    "launch_strategy": self.launch_strategy,
                },
            )

        self._started = True
        self._closed = False
        self._stderr_lines = []
        self._pending_events = []
        self._session_id = self._resolve_resume_session_id(config)
        self._extra_args = self._resolve_extra_args(config)
        self._last_command = []
        self._last_failure = None
        self._active_pid = None
        self._current_turn_index = None
        self._requested_model = self._resolve_requested_value(config, "model")
        self._requested_effort = self._resolve_requested_value(config, "effort")
        self._effective_model = None
        self._effective_effort = None

        return SessionStartResult(
            session_id=self._session_id or "",
            events=(),
            metadata={
                "provider": self.provider,
                "ok": True,
                **self._runtime_debug_metadata(),
                "capability": self.capability,
                "continuity_mode": self.continuity_mode,
                "launch_strategy": self.launch_strategy,
                "note": (
                    "codex continuity is resume-based; each turn runs via "
                    "'codex exec' and does not keep a single long-lived stdin loop"
                ),
            },
        )

    async def send_turn(self, turn: SessionTurn) -> None:
        if not self._started or self._closed:
            raise RuntimeError("codex adapter has not been started")

        async with self._turn_lock:
            turn_index = turn.metadata.get("turn_index")
            self._current_turn_index = turn_index if isinstance(turn_index, int) else None
            command = self._build_launch_command(self._session_id)
            self._last_command = command

            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except Exception as exc:
                self._set_failure("launch_failed", str(exc))
                raise
            self._active_process = process
            self._active_pid = process.pid if isinstance(process.pid, int) else None
            stdout_task: asyncio.Task[list[str]] | None = None
            stderr_task: asyncio.Task[list[str]] | None = None

            try:
                if process.stdin is None:
                    raise RuntimeError("codex process does not expose stdin")

                prompt_text = turn.content if turn.content.endswith("\n") else f"{turn.content}\n"
                process.stdin.write(prompt_text.encode("utf-8"))
                drain = getattr(process.stdin, "drain", None)
                if drain is not None:
                    drained = drain()
                    if asyncio.iscoroutine(drained):
                        await drained
                process.stdin.close()

                stdout_task = asyncio.create_task(self._collect_stream_lines(process.stdout))
                stderr_task = asyncio.create_task(self._collect_stream_lines(process.stderr))
                try:
                    await asyncio.wait_for(process.wait(), timeout=self._turn_timeout)
                except TimeoutError as exc:
                    await self._stop_process(process)
                    stdout_lines = await self._collect_reader_output(stdout_task)
                    stderr_lines = await self._collect_reader_output(stderr_task)
                    for line in stderr_lines:
                        if line:
                            self._stderr_lines.append(line)
                    self._set_failure(
                        "turn_timeout",
                        f"codex exec timed out after {self._turn_timeout:.2f}s",
                    )
                    raise TimeoutError("codex exec turn timed out") from exc

                stdout_lines = await self._collect_reader_output(stdout_task)
                stderr_lines = await self._collect_reader_output(stderr_task)

                for line in stderr_lines:
                    if line:
                        self._stderr_lines.append(line)

                for line in stdout_lines:
                    event = self.parse_event_line(line)
                    if event is not None:
                        self._pending_events.append(event)

                if getattr(process, "returncode", 0) != 0:
                    stderr_text = "; ".join(stderr_lines) if stderr_lines else "no stderr"
                    message = f"codex exec exited with code {process.returncode}: {stderr_text}"
                    self._set_failure("turn_failed", message)
                    raise RuntimeError(message)
                self._last_failure = None
            except TimeoutError:
                raise
            except Exception as exc:
                if process.returncode is None:
                    await self._stop_process(process)
                if stdout_task is not None:
                    await self._collect_reader_output(stdout_task)
                if stderr_task is not None:
                    stderr_lines = await self._collect_reader_output(stderr_task)
                    for line in stderr_lines:
                        if line:
                            self._stderr_lines.append(line)
                if self._last_failure is None:
                    self._set_failure("turn_failed", str(exc))
                raise
            finally:
                self._active_process = None

    def read_events(self):
        async def iterator():
            while self._pending_events:
                yield self._pending_events.pop(0)

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
                },
            )

        if not self._started:
            return SessionHealth(
                ok=False,
                message="not started",
                metadata={
                    "provider": self.provider,
                    "state": "not_started",
                    **self._runtime_debug_metadata(),
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                },
            )

        if self._last_failure is not None:
            return SessionHealth(
                ok=False,
                message=self._last_failure["message"],
                metadata={
                    "provider": self.provider,
                    "state": "error",
                    **self._runtime_debug_metadata(),
                    "failure_kind": self._last_failure["kind"],
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                },
            )

        if self._active_process is not None and self._active_process.returncode is None:
            return SessionHealth(
                ok=True,
                message=None,
                metadata={
                    "provider": self.provider,
                    "state": "executing_turn",
                    **self._runtime_debug_metadata(),
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                },
            )

        return SessionHealth(
            ok=True,
            message=None,
            metadata={
                "provider": self.provider,
                "state": "ready_for_turn",
                **self._runtime_debug_metadata(),
                "capability": self.capability,
                "continuity_mode": self.continuity_mode,
            },
        )

    async def close(self) -> SessionCloseResult:
        if not self._started:
            return SessionCloseResult(
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

        ok = True
        message: str | None = None
        active = self._active_process
        if active is not None and active.returncode is None:
            stopped, stop_errors = await self._stop_process(active)
            if not stopped:
                ok = False
                message = "; ".join(stop_errors) if stop_errors else "failed to stop codex process"

        self._active_process = None
        self._started = False
        self._closed = True
        return SessionCloseResult(
            ok=ok,
            message=message,
            metadata={
                "provider": self.provider,
                **self._runtime_debug_metadata(),
                "command": list(self._last_command),
                "stderr_lines": list(self._stderr_lines),
                "error_kind": "closed" if ok else "shutdown_failed",
                "capability": self.capability,
                "continuity_mode": self.continuity_mode,
                "launch_strategy": self.launch_strategy,
            },
        )

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
                        pid=self._active_pid,
                        session_id=self._session_id,
                        turn=self._current_turn_index,
                        requested_model=self._requested_model,
                        requested_effort=self._requested_effort,
                        effective_model=self._effective_model,
                        effective_effort=self._effective_effort,
                    ),
                    "reason": "malformed_json",
                    "provider": self.provider,
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                },
            )

        self._ingest_effective_metadata(data)
        event_type = data.get("type")
        pid = self._active_pid
        turn_index = self._current_turn_index
        if event_type == "thread.started":
            thread_id = data.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                self._session_id = thread_id
            return SessionEvent(
                kind="session_started",
                content=self._session_id,
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
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                },
            )

        if event_type == "turn.started":
            return SessionEvent(
                kind="turn_started",
                content=str(data.get("turn_id", "")) or None,
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
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                },
            )

        if event_type == "item.completed":
            item = data.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                return SessionEvent(
                    kind="text_delta",
                    content=text if isinstance(text, str) else None,
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
                        "capability": self.capability,
                        "continuity_mode": self.continuity_mode,
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
                    "reason": "non_agent_message_item",
                    "raw": data,
                    "provider": self.provider,
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
                },
            )

        if event_type == "turn.completed":
            return SessionEvent(
                kind="turn_complete",
                content=str(data.get("turn_id", "")) or None,
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
                    "capability": self.capability,
                    "continuity_mode": self.continuity_mode,
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
                "reason": "unknown_event",
                "raw": data,
                "provider": self.provider,
                "capability": self.capability,
                "continuity_mode": self.continuity_mode,
            },
        )

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

    def _resolve_extra_args(self, config: SessionConfig) -> list[str]:
        args = config.options.get("args")
        if not isinstance(args, (list, tuple)):
            return []
        return [str(arg) for arg in args]

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

    def _runtime_debug_metadata(self) -> dict[str, Any]:
        return build_runtime_debug_session_metadata(
            provider=self.provider,
            pid=self._active_pid,
            session_id=self._session_id,
            turn=self._current_turn_index,
            requested_model=self._requested_model,
            requested_effort=self._requested_effort,
            effective_model=self._effective_model,
            effective_effort=self._effective_effort,
        )

    def _build_launch_command(self, session_id: str | None) -> list[str]:
        if session_id:
            command = [self._command, "exec", "resume", session_id, "--json"]
        else:
            command = [self._command, "exec", "--json"]
        command.append("--skip-git-repo-check")
        command.extend(self._extra_args)
        return command

    async def _collect_stream_lines(self, stream: Any) -> list[str]:
        if stream is None:
            return []
        lines: list[str] = []
        async for raw_line in stream:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if line:
                lines.append(line)
        return lines

    async def _collect_reader_output(
        self, task: asyncio.Task[list[str]] | None
    ) -> list[str]:
        if task is None:
            return []
        try:
            return await asyncio.wait_for(task, timeout=self._close_timeout)
        except asyncio.CancelledError:
            return []
        except TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            return []

    async def _stop_process(
        self, process: asyncio.subprocess.Process
    ) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if process.returncode is not None:
            return (True, errors)

        try:
            process.terminate()
        except Exception as exc:  # noqa: BLE001 - explicit prototype shutdown reporting
            errors.append(f"terminate failed: {exc}")

        try:
            await asyncio.wait_for(process.wait(), timeout=self._close_timeout)
        except TimeoutError:
            try:
                process.kill()
            except Exception as exc:  # noqa: BLE001 - explicit prototype shutdown reporting
                errors.append(f"kill failed: {exc}")
            try:
                await asyncio.wait_for(process.wait(), timeout=self._close_timeout)
            except Exception as exc:  # noqa: BLE001 - explicit prototype shutdown reporting
                errors.append(f"wait after kill failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - explicit prototype shutdown reporting
            errors.append(f"wait after terminate failed: {exc}")

        return (process.returncode is not None, errors)

    def _set_failure(self, kind: str, message: str) -> None:
        self._last_failure = {"kind": kind, "message": message}

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
