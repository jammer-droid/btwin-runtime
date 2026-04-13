from __future__ import annotations

import asyncio
import json
from argparse import ArgumentParser
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from time import monotonic
from typing import Any, Iterable

from btwin_core.prototypes.persistent_sessions.base import PersistentSessionAdapter
from btwin_core.prototypes.persistent_sessions.claude_adapter import ClaudePersistentAdapter
from btwin_core.prototypes.persistent_sessions.codex_app_server_adapter import (
    CodexAppServerPersistentAdapter,
)
from btwin_core.prototypes.persistent_sessions.codex_adapter import CodexPersistentAdapter
from btwin_core.prototypes.persistent_sessions.types import (
    SessionCloseResult,
    SessionConfig,
    SessionEvent,
    SessionHealth,
    SessionStartResult,
    SessionTurn,
)


@dataclass(slots=True)
class PrototypeHarnessResult:
    provider: str
    session_id: str | None
    turns_sent: int
    event_count: int
    health_ok: bool
    close_ok: bool
    status: str
    capability: str | None = None
    continuity_mode: str | None = None
    launch_strategy: str | None = None
    error: str | None = None
    turn_summaries: tuple[dict[str, Any], ...] = ()
    start_metadata: dict[str, Any] = field(default_factory=dict)
    health_metadata: dict[str, Any] = field(default_factory=dict)
    close_metadata: dict[str, Any] = field(default_factory=dict)


class PrototypeHarness:
    DEFAULT_TURNS: tuple[str, str] = (
        (
            "This is a btwin persistent-session prototype check. Remember the token "
            "BTWIN-PERSIST-ALPHA and reply exactly with: TOKEN BTWIN-PERSIST-ALPHA"
        ),
        "What token should you remember from the previous turn? Reply exactly with: TOKEN BTWIN-PERSIST-ALPHA",
    )
    CODEX_APP_SERVER_TURNS: tuple[str, str] = (
        (
            "Reply with exactly: TOKEN BTWIN-PERSIST-ALPHA. "
            "Do not use tools. Do not read files. Do not explain. "
            "Do not invoke skills. Output only the exact text."
        ),
        (
            "What exact token did you return previously? "
            "Reply with exactly: TOKEN BTWIN-PERSIST-ALPHA. "
            "Do not use tools. Do not read files. Do not explain. "
            "Do not invoke skills. Output only the exact text."
        ),
    )
    _CODEX_IDLE_COMPLETION_GRACE_SECONDS = 1.0

    def __init__(
        self,
        adapter: PersistentSessionAdapter,
        *,
        config: SessionConfig | None = None,
        max_events_per_turn: int = 64,
        event_idle_timeout: float = 6.0,
        turn_completion_timeout: float = 30.0,
        terminal_event_kinds: Iterable[str] | None = None,
        turns: tuple[str, str] | None = None,
    ) -> None:
        self._adapter = adapter
        self._config = config or SessionConfig()
        self._max_events_per_turn = max_events_per_turn
        self._event_idle_timeout = event_idle_timeout
        self._turn_completion_timeout = turn_completion_timeout
        self._terminal_event_kinds = frozenset(
            terminal_event_kinds or {"complete", "done", "final", "turn_complete"}
        )
        self._turns = turns or self._default_turns()

    def _default_turns(self) -> tuple[str, str]:
        provider = getattr(self._adapter, "provider", None)
        if provider == "codex-app-server":
            return self.CODEX_APP_SERVER_TURNS
        return self.DEFAULT_TURNS

    async def run_standard_scenario(self) -> PrototypeHarnessResult:
        provider = self._provider_name()
        session_id: str | None = None
        turns_sent = 0
        event_count = 0
        health = SessionHealth(ok=False)
        close = SessionCloseResult(ok=False)
        error: str | None = None
        turn_events: list[tuple[SessionEvent, ...]] = []
        start_metadata: dict[str, Any] = {}

        try:
            start_result = await self._adapter.start(self._config)
            session_id = start_result.session_id
            start_metadata = dict(start_result.metadata)
            provider = self._provider_from_start_result(start_result, provider)
            if start_metadata.get("ok") is False:
                message = start_metadata.get("message")
                if isinstance(message, str) and message:
                    raise RuntimeError(message)
                raise RuntimeError("adapter start failed")

            first_turn_events = await self._send_turn_and_collect_events(self._turns[0], 1)
            turns_sent += 1
            event_count += len(first_turn_events)
            turn_events.append(first_turn_events)

            second_turn_events = await self._send_turn_and_collect_events(self._turns[1], 2)
            turns_sent += 1
            event_count += len(second_turn_events)
            turn_events.append(second_turn_events)

            health = await self._adapter.health_check()
        except Exception as exc:  # noqa: BLE001 - prototype harness captures failure signals
            error = str(exc)
            if turns_sent > 0:
                try:
                    event_count += len(await self._drain_events())
                except Exception as drain_exc:  # noqa: BLE001 - best-effort read cleanup
                    if error is None:
                        error = str(drain_exc)
                    else:
                        error = f"{error}; read drain failed: {drain_exc}"
            try:
                health = await self._adapter.health_check()
            except Exception as health_exc:  # noqa: BLE001 - best-effort health probe
                if error is None:
                    error = str(health_exc)
        finally:
            try:
                close = await self._adapter.close()
            except Exception as close_exc:  # noqa: BLE001 - best-effort shutdown
                close = SessionCloseResult(ok=False, message=str(close_exc))
                if error is None:
                    error = str(close_exc)

        status = self._resolve_status(
            error=error,
            turns_sent=turns_sent,
            health=health,
            close=close,
            turn_events=turn_events,
            start_metadata=start_metadata,
        )
        return PrototypeHarnessResult(
            provider=provider,
            session_id=session_id,
            turns_sent=turns_sent,
            event_count=event_count,
            health_ok=health.ok,
            close_ok=close.ok,
            status=status,
            capability=self._capability(start_metadata),
            continuity_mode=self._continuity_mode(start_metadata),
            launch_strategy=self._launch_strategy(start_metadata),
            error=error,
            turn_summaries=tuple(
                self._summarize_turn_events(events, turn_index)
                for turn_index, events in enumerate(turn_events, start=1)
            ),
            start_metadata=start_metadata,
            health_metadata=dict(health.metadata),
            close_metadata=dict(close.metadata),
        )

    async def _send_turn_and_collect_events(
        self, content: str, turn_index: int
    ) -> tuple[SessionEvent, ...]:
        await self._adapter.send_turn(
            SessionTurn(content=content, metadata={"turn_index": turn_index})
        )
        return await self._drain_events()

    async def _drain_events(self) -> tuple[SessionEvent, ...]:
        events: list[SessionEvent] = []
        iterator = self._adapter.read_events()
        started_turn_id: str | None = None
        completion_deadline = monotonic() + self._turn_completion_timeout
        idle_completion_deadline: float | None = None
        next_event_task: asyncio.Task[SessionEvent] | None = None

        try:
            for _ in range(self._max_events_per_turn):
                timeout_seconds = self._event_idle_timeout
                if started_turn_id is not None:
                    remaining = completion_deadline - monotonic()
                    if remaining <= 0:
                        break
                    timeout_seconds = min(timeout_seconds, remaining)
                if idle_completion_deadline is not None:
                    idle_remaining = idle_completion_deadline - monotonic()
                    if idle_remaining <= 0:
                        events.append(
                            self._make_codex_idle_fallback_completion(started_turn_id)
                        )
                        break
                    timeout_seconds = min(timeout_seconds, idle_remaining)

                if next_event_task is None:
                    next_event_task = asyncio.create_task(anext(iterator))

                try:
                    event = await asyncio.wait_for(
                        asyncio.shield(next_event_task),
                        timeout=timeout_seconds,
                    )
                except StopAsyncIteration:
                    next_event_task = None
                    if idle_completion_deadline is not None:
                        events.append(
                            self._make_codex_idle_fallback_completion(started_turn_id)
                        )
                    break
                except TimeoutError:
                    if idle_completion_deadline is not None and monotonic() >= idle_completion_deadline:
                        next_event_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await next_event_task
                        next_event_task = None
                        events.append(
                            self._make_codex_idle_fallback_completion(started_turn_id)
                        )
                        break
                    if started_turn_id is not None and monotonic() < completion_deadline:
                        continue
                    next_event_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await next_event_task
                    next_event_task = None
                    break

                next_event_task = None
                events.append(event)
                if event.kind == "turn_started" and isinstance(event.content, str) and event.content:
                    started_turn_id = event.content
                    idle_completion_deadline = None
                if self._matches_terminal_turn(event, started_turn_id):
                    break
                if self._is_codex_app_server_idle_event(event, started_turn_id):
                    idle_completion_deadline = min(
                        completion_deadline,
                        monotonic()
                        + max(
                            self._CODEX_IDLE_COMPLETION_GRACE_SECONDS,
                            self._event_idle_timeout,
                        ),
                    )
        finally:
            if next_event_task is not None:
                next_event_task.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_event_task

        return tuple(events)

    def _matches_terminal_turn(
        self, event: SessionEvent, started_turn_id: str | None
    ) -> bool:
        if event.kind not in self._terminal_event_kinds:
            return False
        metadata = event.metadata
        provider = metadata.get("provider") if isinstance(metadata, dict) else None
        if started_turn_id is None:
            if provider == "codex-app-server":
                return False
            return True
        if provider != "codex-app-server":
            return True
        event_turn_id = event.content
        if isinstance(event_turn_id, str) and event_turn_id:
            return event_turn_id == started_turn_id
        return False

    def _is_codex_app_server_idle_event(
        self, event: SessionEvent, started_turn_id: str | None
    ) -> bool:
        if started_turn_id is None:
            return False
        metadata = event.metadata
        if not isinstance(metadata, dict):
            return False
        if metadata.get("provider") != "codex-app-server":
            return False
        raw = metadata.get("raw")
        if not isinstance(raw, dict):
            return False
        if raw.get("method") != "thread/status/changed":
            return False
        params = raw.get("params")
        if not isinstance(params, dict):
            return False
        status = params.get("status")
        if not isinstance(status, dict):
            return False
        return status.get("type") == "idle"

    def _make_codex_idle_fallback_completion(self, started_turn_id: str | None) -> SessionEvent:
        return SessionEvent(
            kind="turn_complete",
            content=started_turn_id,
            metadata={
                "provider": "codex-app-server",
                "synthetic": True,
                "reason": "idle_fallback",
            },
        )

    def _provider_name(self) -> str:
        provider = getattr(self._adapter, "provider", None)
        return provider if isinstance(provider, str) and provider else "unknown"

    def _provider_from_start_result(
        self, start_result: SessionStartResult, default: str
    ) -> str:
        provider = start_result.metadata.get("provider") if start_result.metadata else None
        return provider if isinstance(provider, str) and provider else default

    def _resolve_status(
        self,
        *,
        error: str | None,
        turns_sent: int,
        health: SessionHealth,
        close: SessionCloseResult,
        turn_events: list[tuple[SessionEvent, ...]],
        start_metadata: dict[str, Any] | None = None,
    ) -> str:
        if error is not None:
            return "failed"
        if turns_sent != 2:
            return "failed"
        if not health.ok or not close.ok:
            return "failed"
        if not turn_events or any(not self._turn_completed(events) for events in turn_events):
            return "failed"
        capability = self._capability(start_metadata or {})
        if capability == "partial_persistent":
            return "partial"
        return "works"

    def _turn_completed(self, events: tuple[SessionEvent, ...]) -> bool:
        started_turn_id: str | None = None
        for event in events:
            if event.kind == "turn_started" and isinstance(event.content, str) and event.content:
                started_turn_id = event.content
            if self._matches_terminal_turn(event, started_turn_id):
                return True
        return False

    def _capability(self, metadata: dict[str, Any]) -> str | None:
        capability = metadata.get("capability")
        if isinstance(capability, str) and capability:
            return capability
        adapter_capability = getattr(self._adapter, "capability", None)
        return (
            adapter_capability
            if isinstance(adapter_capability, str) and adapter_capability
            else None
        )

    def _continuity_mode(self, metadata: dict[str, Any]) -> str | None:
        continuity_mode = metadata.get("continuity_mode")
        if isinstance(continuity_mode, str) and continuity_mode:
            return continuity_mode
        adapter_continuity_mode = getattr(self._adapter, "continuity_mode", None)
        return (
            adapter_continuity_mode
            if isinstance(adapter_continuity_mode, str) and adapter_continuity_mode
            else None
        )

    def _launch_strategy(self, metadata: dict[str, Any]) -> str | None:
        launch_strategy = metadata.get("launch_strategy")
        if isinstance(launch_strategy, str) and launch_strategy:
            return launch_strategy
        adapter_launch_strategy = getattr(self._adapter, "launch_strategy", None)
        return (
            adapter_launch_strategy
            if isinstance(adapter_launch_strategy, str) and adapter_launch_strategy
            else None
        )

    def _summarize_turn_events(
        self, events: tuple[SessionEvent, ...], turn_index: int
    ) -> dict[str, Any]:
        return {
            "turn_index": turn_index,
            "event_kinds": [event.kind for event in events],
            "text_fragments": [event.content for event in events if event.content],
        }


def _resolve_provider_timeouts(
    *,
    provider: str,
    start_timeout: float | None,
    close_timeout: float | None,
) -> tuple[float, float]:
    if provider == "claude":
        return (
            10.0 if start_timeout is None else start_timeout,
            2.0 if close_timeout is None else close_timeout,
        )
    if provider == "codex-app-server":
        return (
            10.0 if start_timeout is None else start_timeout,
            2.0 if close_timeout is None else close_timeout,
        )
    return (
        0.2 if start_timeout is None else start_timeout,
        0.2 if close_timeout is None else close_timeout,
    )


async def run_provider_scenario(
    *,
    provider: str,
    provider_command: str | None = None,
    provider_args: list[str] | None = None,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    start_timeout: float | None = None,
    turn_timeout: float = 30.0,
    close_timeout: float | None = None,
    event_idle_timeout: float = 6.0,
) -> PrototypeHarnessResult:
    resolved_start_timeout, resolved_close_timeout = _resolve_provider_timeouts(
        provider=provider,
        start_timeout=start_timeout,
        close_timeout=close_timeout,
    )
    metadata: dict[str, str] = {}
    if resume_session_id:
        metadata["resume_session_id"] = resume_session_id
    if model:
        metadata["model"] = model
    if effort:
        metadata["effort"] = effort
    config = SessionConfig(options={"args": list(provider_args or [])}, metadata=metadata)

    if provider == "claude":
        adapter: PersistentSessionAdapter = ClaudePersistentAdapter(
            command=provider_command or "claude",
            start_timeout=resolved_start_timeout,
            close_timeout=resolved_close_timeout,
        )
    elif provider == "codex":
        adapter = CodexPersistentAdapter(
            command=provider_command or "codex",
            turn_timeout=turn_timeout,
            close_timeout=resolved_close_timeout,
        )
    elif provider == "codex-app-server":
        adapter = CodexAppServerPersistentAdapter(
            command=provider_command or "codex",
            start_timeout=resolved_start_timeout,
            close_timeout=resolved_close_timeout,
        )
    else:
        raise ValueError(f"unsupported provider: {provider}")

    harness = PrototypeHarness(
        adapter,
        config=config,
        event_idle_timeout=event_idle_timeout,
        turn_completion_timeout=turn_timeout,
    )
    return await harness.run_standard_scenario()


def build_argument_parser() -> ArgumentParser:
    parser = ArgumentParser(description="Run the persistent-session prototype harness.")
    parser.add_argument(
        "--provider",
        choices=("claude", "codex", "codex-app-server"),
        required=True,
    )
    parser.add_argument("--provider-command")
    parser.add_argument("--provider-arg", action="append", default=[])
    parser.add_argument("--resume-session-id")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--start-timeout", type=float)
    parser.add_argument("--turn-timeout", type=float, default=30.0)
    parser.add_argument("--close-timeout", type=float)
    parser.add_argument("--event-idle-timeout", type=float, default=6.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    resolved_start_timeout, resolved_close_timeout = _resolve_provider_timeouts(
        provider=args.provider,
        start_timeout=args.start_timeout,
        close_timeout=args.close_timeout,
    )
    result = asyncio.run(
        run_provider_scenario(
            provider=args.provider,
            provider_command=args.provider_command,
            provider_args=args.provider_arg,
            resume_session_id=args.resume_session_id,
            model=args.model,
            effort=args.effort,
            start_timeout=resolved_start_timeout,
            turn_timeout=args.turn_timeout,
            close_timeout=resolved_close_timeout,
            event_idle_timeout=args.event_idle_timeout,
        )
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
