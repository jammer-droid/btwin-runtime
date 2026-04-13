from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

_RUNTIME_DEBUG_SESSION_KEYS = (
    "provider",
    "pid",
    "session_id",
    "turn",
    "requested_model",
    "requested_effort",
    "effective_model",
    "effective_effort",
)


@dataclass(slots=True)
class SessionConfig:
    options: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionTurn:
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionEvent:
    kind: str
    content: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionStartResult:
    session_id: str
    events: tuple[SessionEvent, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionHealth:
    ok: bool
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionCloseResult:
    ok: bool
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeDebugSessionMetadata:
    provider: str
    pid: int | None = None
    session_id: str | None = None
    turn: int | None = None
    requested_model: str | None = None
    requested_effort: str | None = None
    effective_model: str | None = None
    effective_effort: str | None = None


def build_runtime_debug_session_metadata(
    *,
    provider: str,
    pid: int | None = None,
    session_id: str | None = None,
    turn: int | None = None,
    requested_model: str | None = None,
    requested_effort: str | None = None,
    effective_model: str | None = None,
    effective_effort: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "pid": pid,
        "session_id": session_id,
        "turn": turn,
        "requested_model": requested_model,
        "requested_effort": requested_effort,
        "effective_model": effective_model,
        "effective_effort": effective_effort,
    }


def format_runtime_debug_session_metadata(
    metadata: RuntimeDebugSessionMetadata | Mapping[str, Any],
) -> str:
    values = (
        {
            key: getattr(metadata, key)
            for key in _RUNTIME_DEBUG_SESSION_KEYS
            if hasattr(metadata, key)
        }
        if isinstance(metadata, RuntimeDebugSessionMetadata)
        else dict(metadata)
    )
    parts = [
        f"{key}={_format_runtime_debug_value(values.get(key))}"
        for key in (
            "provider",
            "pid",
            "session_id",
            "turn",
            "requested_model",
            "requested_effort",
            "effective_model",
            "effective_effort",
        )
    ]
    return "[debug-session] " + " ".join(parts)


def _format_runtime_debug_value(value: Any) -> str:
    if value is None:
        return "null"
    return str(value)
