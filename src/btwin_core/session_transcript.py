"""Normalize noisy provider runtime events into btwin-owned transcript events."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any, Iterable


_SESSION_STARTED_KINDS = {
    "session_started",
    "thread.started",
    "thread/started",
    "system",
    "system/init",
    "init",
}
_TEXT_DELTA_KINDS = {
    "text_delta",
    "assistant",
    "item/agentMessage/delta",
}
_TURN_COMPLETE_KINDS = {
    "turn_complete",
    "turn.completed",
    "turn/completed",
    "complete",
    "done",
    "final",
    "result",
}


@dataclass(slots=True)
class NormalizedRuntimeEvent:
    kind: str
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_runtime_events(events: Iterable[Any], *, provider_name: str | None = None) -> list[NormalizedRuntimeEvent]:
    """Return only the transcript-worthy runtime events in order."""
    normalized: list[NormalizedRuntimeEvent] = []
    for event in events:
        normalized.extend(_normalize_runtime_event(event, provider_name=provider_name))
    return normalized


def _normalize_runtime_event(
    event: Any,
    *,
    provider_name: str | None = None,
) -> list[NormalizedRuntimeEvent]:
    kind = _event_kind(event)
    metadata = _event_metadata(event)
    if provider_name and "provider" not in metadata:
        metadata["provider"] = provider_name
    content = _event_content(event)

    if kind in _SESSION_STARTED_KINDS:
        session_id = _event_session_id(event) or content
        return [NormalizedRuntimeEvent(kind="session_started", content=session_id, metadata=metadata)]

    if kind in _TEXT_DELTA_KINDS:
        if not content:
            return []
        return [NormalizedRuntimeEvent(kind="text_delta", content=content, metadata=metadata)]

    if kind in _TURN_COMPLETE_KINDS:
        return [NormalizedRuntimeEvent(kind="turn_complete", content=content, metadata=metadata)]

    return []


def _event_kind(event: Any) -> str:
    if isinstance(event, Mapping):
        for key in ("kind", "event_type", "type"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        return ""
    for attr in ("kind", "event_type", "type"):
        value = getattr(event, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _event_content(event: Any) -> str | None:
    if isinstance(event, Mapping):
        for key in ("content", "final_text", "text_delta"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        message = event.get("message")
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list):
                delta = "".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, Mapping) and block.get("type") == "text"
                )
                if delta:
                    return delta
        return None
    value = getattr(event, "content", None)
    if isinstance(value, str) and value:
        return value
    value = getattr(event, "final_text", None)
    if isinstance(value, str) and value:
        return value
    value = getattr(event, "text_delta", None)
    if isinstance(value, str) and value:
        return value
    return None


def _event_session_id(event: Any) -> str | None:
    if isinstance(event, Mapping):
        value = event.get("session_id")
        if isinstance(value, str) and value:
            return value
        return None
    value = getattr(event, "session_id", None)
    if isinstance(value, str) and value:
        return value
    return None


def _event_metadata(event: Any) -> dict[str, Any]:
    if isinstance(event, Mapping):
        metadata: dict[str, Any] = {}
        nested_metadata = event.get("metadata")
        if isinstance(nested_metadata, Mapping):
            metadata.update(nested_metadata)
        for key, value in event.items():
            if key in {"kind", "event_type", "type", "content", "final_text", "text_delta", "session_id", "metadata"}:
                continue
            if value is not None:
                metadata[key] = value
        return metadata
    value = getattr(event, "metadata", None)
    if isinstance(value, dict):
        return dict(value)
    value = getattr(event, "raw", None)
    if isinstance(value, dict):
        metadata: dict[str, Any] = {}
        nested_metadata = value.get("metadata")
        if isinstance(nested_metadata, dict):
            metadata.update(nested_metadata)
        for key, item in value.items():
            if key in {"kind", "event_type", "type", "content", "final_text", "text_delta", "session_id", "metadata"}:
                continue
            if item is not None:
                metadata[key] = item
        return metadata or {"raw": value}
    return {}
