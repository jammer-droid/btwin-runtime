"""Normalize noisy provider runtime events into btwin-owned transcript events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
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
    "item.completed",
    "complete",
    "done",
    "final",
    "result",
}
_NOISE_MARKERS = {
    "hook",
    "notification",
    "status",
    "status_notification",
    "tool_use",
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

    if _event_has_noise_marker(event):
        return []

    if kind in _SESSION_STARTED_KINDS:
        session_id = _event_session_id(event)
        if not session_id:
            return []
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
        for key in ("content", "final_text", "text_delta", "delta", "result", "text"):
            content = _extract_text_content(event.get(key))
            if content:
                return content
        for key in ("message", "item", "raw"):
            nested = event.get(key)
            if isinstance(nested, Mapping):
                content = _event_content(nested)
                if content:
                    return content
        return None
    for attr in ("content", "final_text", "text_delta", "delta", "result", "text"):
        content = _extract_text_content(getattr(event, attr, None))
        if content:
            return content
    raw = getattr(event, "raw", None)
    if isinstance(raw, Mapping):
        return _event_content(raw)
    return None


def _event_session_id(event: Any) -> str | None:
    if isinstance(event, Mapping):
        for key in ("session_id", "thread_id"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        for key in ("session", "thread"):
            value = event.get(key)
            if isinstance(value, Mapping):
                nested = value.get("id")
                if isinstance(nested, str) and nested:
                    return nested
        raw = event.get("raw")
        if isinstance(raw, Mapping):
            nested = _event_session_id(raw)
            if nested:
                return nested
        return None
    value = getattr(event, "session_id", None)
    if isinstance(value, str) and value:
        return value
    value = getattr(event, "thread_id", None)
    if isinstance(value, str) and value:
        return value
    for attr in ("session", "thread"):
        value = getattr(event, attr, None)
        if isinstance(value, Mapping):
            nested = value.get("id")
            if isinstance(nested, str) and nested:
                return nested
    raw = getattr(event, "raw", None)
    if isinstance(raw, Mapping):
        nested = _event_session_id(raw)
        if nested:
            return nested
    return None


def _extract_text_content(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        delta = "".join(
            block.get("text", "")
            for block in value
            if isinstance(block, Mapping) and block.get("type") == "text"
        )
        if delta:
            return delta
    if isinstance(value, Mapping):
        for key in ("text", "content"):
            nested = _extract_text_content(value.get(key))
            if nested:
                return nested
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


def _event_has_noise_marker(event: Any) -> bool:
    for value in _event_noise_values(event):
        if _is_noise_marker(value):
            return True
    return False


def _event_noise_values(event: Any) -> Iterable[Any]:
    if isinstance(event, Mapping):
        for key in ("kind", "event_type", "type", "method"):
            yield event.get(key)
        metadata = event.get("metadata")
        if isinstance(metadata, Mapping):
            for key in ("source", "phase", "name"):
                yield metadata.get(key)
        for key in ("raw", "message", "item"):
            nested = event.get(key)
            if nested is not None:
                yield from _event_noise_values(nested)
        return

    for attr in ("kind", "event_type", "type", "method"):
        yield getattr(event, attr, None)
    metadata = getattr(event, "metadata", None)
    if isinstance(metadata, Mapping):
        for key in ("source", "phase", "name"):
            yield metadata.get(key)
    for attr in ("raw", "message", "item"):
        nested = getattr(event, attr, None)
        if nested is not None:
            yield from _event_noise_values(nested)


def _is_noise_marker(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    tokens = [token for token in re.split(r"[^a-z0-9]+", value.lower()) if token]
    return any(token in _NOISE_MARKERS for token in tokens)
