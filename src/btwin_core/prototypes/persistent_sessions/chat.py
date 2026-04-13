from __future__ import annotations

import asyncio
import sys
from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Callable, Mapping, TextIO

from btwin_core.prototypes.persistent_sessions.base import PersistentSessionAdapter
from btwin_core.prototypes.persistent_sessions.claude_adapter import ClaudePersistentAdapter
from btwin_core.prototypes.persistent_sessions.codex_app_server_adapter import (
    CodexAppServerPersistentAdapter,
)
from btwin_core.prototypes.persistent_sessions.types import (
    build_runtime_debug_session_metadata,
    format_runtime_debug_session_metadata,
    SessionCloseResult,
    SessionConfig,
    SessionEvent,
    SessionStartResult,
    SessionTurn,
)

_DEFAULT_EXIT_COMMANDS = frozenset({"/exit", "/quit"})
_DEFAULT_TERMINAL_EVENT_KINDS = frozenset({"complete", "done", "final", "turn_complete"})
_GENERIC_COMPLETE_CONTENT = frozenset({"done", "ok", "ready"})


@dataclass(slots=True)
class ChatSessionResult:
    session_id: str | None
    turns_sent: int
    close_ok: bool
    exit_reason: str
    provider: str
    error: str | None = None


class _AssistantTurnRenderer:
    def __init__(self, output_stream: TextIO) -> None:
        self._output_stream = output_stream
        self._printed_prefix = False
        self._rendered_text = ""

    def write_stream(self, content: str | None) -> None:
        self._write_content(content, allow_generic=True)

    def write_complete(self, content: str | None) -> None:
        if content is None:
            return
        if not self._rendered_text and content.strip().lower() in _GENERIC_COMPLETE_CONTENT:
            return
        self._write_content(content, allow_generic=False)

    def finish(self) -> None:
        if self._printed_prefix:
            _write(self._output_stream, "\n")

    def _write_content(self, content: str | None, *, allow_generic: bool) -> None:
        if not content:
            return
        text = content
        if not allow_generic and not self._rendered_text and text.strip().lower() in _GENERIC_COMPLETE_CONTENT:
            return

        novel_text = _novel_text(self._rendered_text, text)
        if not novel_text:
            return
        self._ensure_prefix()
        _write(self._output_stream, novel_text)
        self._rendered_text += novel_text

    def _ensure_prefix(self) -> None:
        if self._printed_prefix:
            return
        _write(self._output_stream, "assistant> ")
        self._printed_prefix = True


def _write(output_stream: TextIO, text: str) -> None:
    output_stream.write(text)
    flush = getattr(output_stream, "flush", None)
    if callable(flush):
        flush()


def _write_line(output_stream: TextIO, text: str) -> None:
    _write(output_stream, text + "\n")


def _novel_text(rendered_text: str, incoming_text: str) -> str:
    if not rendered_text:
        return incoming_text
    if incoming_text.startswith(rendered_text):
        return incoming_text[len(rendered_text) :]
    if rendered_text.endswith(incoming_text):
        return ""
    return incoming_text


def _resolve_provider(
    adapter: PersistentSessionAdapter,
    start_result: SessionStartResult,
) -> str:
    provider = start_result.metadata.get("provider") if start_result.metadata else None
    if isinstance(provider, str) and provider:
        return provider
    adapter_provider = getattr(adapter, "provider", None)
    return adapter_provider if isinstance(adapter_provider, str) and adapter_provider else "unknown"


def _provider_label(provider: str) -> str:
    if provider == "codex-app-server":
        return "Codex app-server"
    return provider.capitalize()


async def run_chat_session(
    adapter: PersistentSessionAdapter,
    *,
    config: SessionConfig | None = None,
    input_func: Callable[[str], str] = input,
    output_stream: TextIO | None = None,
    exit_commands: frozenset[str] = _DEFAULT_EXIT_COMMANDS,
    terminal_event_kinds: frozenset[str] = _DEFAULT_TERMINAL_EVENT_KINDS,
    debug_session: bool = False,
) -> ChatSessionResult:
    resolved_output = output_stream or sys.stdout
    resolved_config = config or SessionConfig()
    provider = getattr(adapter, "provider", "unknown")
    if not isinstance(provider, str) or not provider:
        provider = "unknown"
    session_id: str | None = None
    turns_sent = 0
    exit_reason = "command"
    error: str | None = None
    close_result = SessionCloseResult(ok=False, message="not closed")

    try:
        start_result = await adapter.start(resolved_config)
        provider = _resolve_provider(adapter, start_result)
        session_id = start_result.session_id or None
        if start_result.metadata.get("ok") is False:
            message = start_result.metadata.get("message")
            error = message if isinstance(message, str) and message else "session start failed"
            exit_reason = "start_failed"
            _write_line(resolved_output, f"{_provider_label(provider)} session failed to start: {error}")
        else:
            _write_line(
                resolved_output,
                f"{_provider_label(provider)} persistent session ready"
                + (f" ({session_id})" if session_id else "")
                + ". Type /exit to quit.",
            )

            while True:
                try:
                    raw_input = input_func("you> ")
                except EOFError:
                    _write_line(resolved_output, "Exiting chat.")
                    exit_reason = "eof"
                    break
                except KeyboardInterrupt:
                    _write_line(resolved_output, "")
                    _write_line(resolved_output, "Exiting chat.")
                    exit_reason = "interrupt"
                    break

                if raw_input.strip().lower() in exit_commands:
                    _write_line(resolved_output, "Exiting chat.")
                    exit_reason = "command"
                    break
                if not raw_input.strip():
                    continue

                turn_index = turns_sent + 1
                await adapter.send_turn(
                    SessionTurn(
                        content=raw_input,
                        metadata={"turn_index": turn_index},
                    )
                )
                turns_sent += 1

                if debug_session:
                    _write_line(
                        resolved_output,
                        format_runtime_debug_session_metadata(
                            _build_debug_session_metadata(
                                adapter=adapter,
                                config=resolved_config,
                                start_metadata=start_result.metadata,
                                provider=provider,
                                session_id=session_id,
                                turn_index=turn_index,
                            )
                        ),
                    )

                renderer = _AssistantTurnRenderer(resolved_output)
                async for event in adapter.read_events():
                    _render_session_event(
                        renderer=renderer,
                        event=event,
                        terminal_event_kinds=terminal_event_kinds,
                    )
                    if event.kind in terminal_event_kinds:
                        break
                renderer.finish()
    except Exception as exc:  # noqa: BLE001 - CLI should surface provider errors directly
        error = str(exc)
        exit_reason = "error"
        _write_line(resolved_output, f"Chat session failed: {error}")
    finally:
        close_result = await adapter.close()
        if not close_result.ok:
            warning = close_result.message or "close failed"
            _write_line(resolved_output, f"Session close warning: {warning}")

    return ChatSessionResult(
        session_id=session_id,
        turns_sent=turns_sent,
        close_ok=close_result.ok,
        exit_reason=exit_reason,
        provider=provider,
        error=error,
    )


def _render_session_event(
    *,
    renderer: _AssistantTurnRenderer,
    event: SessionEvent,
    terminal_event_kinds: frozenset[str],
) -> None:
    if event.kind == "text_delta":
        renderer.write_stream(event.content)
        return
    if event.kind in terminal_event_kinds:
        if event.kind == "turn_complete":
            return
        renderer.write_complete(event.content)


def build_argument_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Talk to a provider through the persistent-session prototype."
    )
    parser.add_argument(
        "--provider",
        choices=("claude", "codex-app-server"),
        default="claude",
    )
    parser.add_argument("--provider-command")
    parser.add_argument("--provider-arg", action="append", default=[])
    parser.add_argument("--resume-session-id")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--debug-session", action="store_true")
    parser.add_argument("--start-timeout", type=float, default=10.0)
    parser.add_argument("--event-timeout", type=float, default=0.05)
    parser.add_argument("--close-timeout", type=float, default=2.0)
    return parser


async def _run_from_args(args) -> ChatSessionResult:
    metadata: dict[str, str] = {}
    if args.resume_session_id:
        metadata["resume_session_id"] = args.resume_session_id
    if args.model:
        metadata["requested_model"] = args.model
        metadata["model"] = args.model
    if args.effort:
        metadata["requested_effort"] = args.effort
        metadata["effort"] = args.effort
    config = SessionConfig(
        options={"args": list(args.provider_arg or [])},
        metadata=metadata,
    )
    if args.provider == "claude":
        adapter: PersistentSessionAdapter = ClaudePersistentAdapter(
            command=args.provider_command or "claude",
            start_timeout=args.start_timeout,
            event_timeout=args.event_timeout,
            close_timeout=args.close_timeout,
        )
    elif args.provider == "codex-app-server":
        adapter = CodexAppServerPersistentAdapter(
            command=args.provider_command or "codex",
            start_timeout=args.start_timeout,
            close_timeout=args.close_timeout,
        )
    else:
        raise ValueError(f"unsupported provider: {args.provider}")
    return await run_chat_session(adapter, config=config, debug_session=args.debug_session)


def _resolve_requested_debug_value(
    config: SessionConfig,
    start_metadata: dict[str, object],
    key: str,
) -> str | None:
    requested_key = f"requested_{key}"
    candidate = config.metadata.get(requested_key)
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    candidate = start_metadata.get(requested_key)
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    candidate = config.metadata.get(key)
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    fallback = start_metadata.get(key)
    if isinstance(fallback, str) and fallback.strip():
        return fallback
    return None


def _resolve_effective_debug_value(start_metadata: dict[str, object], key: str) -> str | None:
    candidate = start_metadata.get(f"effective_{key}")
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    return None


def _build_debug_session_metadata(
    *,
    adapter: PersistentSessionAdapter,
    config: SessionConfig,
    start_metadata: Mapping[str, object],
    provider: str,
    session_id: str | None,
    turn_index: int,
) -> dict[str, object]:
    runtime_metadata = _resolve_adapter_runtime_debug_metadata(adapter)
    return build_runtime_debug_session_metadata(
        provider=_resolve_runtime_debug_string(runtime_metadata, "provider") or provider,
        pid=_resolve_runtime_debug_pid(runtime_metadata) or _resolve_debug_pid(dict(start_metadata)),
        session_id=_resolve_runtime_debug_string(runtime_metadata, "session_id") or session_id,
        turn=_resolve_runtime_debug_turn(runtime_metadata) or turn_index,
        requested_model=_resolve_runtime_debug_string(runtime_metadata, "requested_model")
        or _resolve_requested_debug_value(config, dict(start_metadata), "model"),
        requested_effort=_resolve_runtime_debug_string(runtime_metadata, "requested_effort")
        or _resolve_requested_debug_value(config, dict(start_metadata), "effort"),
        effective_model=_resolve_runtime_debug_string(runtime_metadata, "effective_model")
        or _resolve_effective_debug_value(dict(start_metadata), "model"),
        effective_effort=_resolve_runtime_debug_string(runtime_metadata, "effective_effort")
        or _resolve_effective_debug_value(dict(start_metadata), "effort"),
    )


def _resolve_adapter_runtime_debug_metadata(
    adapter: PersistentSessionAdapter,
) -> Mapping[str, object]:
    runtime_debug_metadata = getattr(adapter, "_runtime_debug_metadata", None)
    if not callable(runtime_debug_metadata):
        return {}
    candidate = runtime_debug_metadata()
    if isinstance(candidate, Mapping):
        return candidate
    return {}


def _resolve_runtime_debug_string(metadata: Mapping[str, object], key: str) -> str | None:
    candidate = metadata.get(key)
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    return None


def _resolve_runtime_debug_pid(metadata: Mapping[str, object]) -> int | None:
    candidate = metadata.get("pid")
    if isinstance(candidate, int):
        return candidate
    return None


def _resolve_runtime_debug_turn(metadata: Mapping[str, object]) -> int | None:
    candidate = metadata.get("turn")
    if isinstance(candidate, int):
        return candidate
    return None


def _resolve_debug_pid(start_metadata: dict[str, object]) -> int | None:
    candidate = start_metadata.get("pid")
    if isinstance(candidate, int):
        return candidate
    if isinstance(candidate, str):
        try:
            return int(candidate)
        except ValueError:
            return None
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    result = asyncio.run(_run_from_args(args))
    if result.error is not None or not result.close_ok or result.exit_reason == "start_failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
