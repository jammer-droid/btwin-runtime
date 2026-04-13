"""Runtime event logging for debugging provider and session behavior."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class RuntimeEventLogger:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "logs" / "runtime-events.jsonl"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        event_type: str,
        *,
        trace_id: str | None = None,
        level: str | None = None,
        message: str | None = None,
        thread_id: str | None = None,
        agent_name: str | None = None,
        provider: str | None = None,
        transport_mode: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traceId": trace_id if trace_id else f"trc_{uuid4().hex[:12]}",
            "eventType": event_type,
        }
        if level is not None:
            event["level"] = level
        if message is not None:
            event["message"] = message
        if thread_id is not None:
            event["threadId"] = thread_id
        if agent_name is not None:
            event["agentName"] = agent_name
        if provider is not None:
            event["provider"] = provider
        if transport_mode is not None:
            event["transportMode"] = transport_mode
        if details is not None:
            event["details"] = details

        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def tail(
        self,
        limit: int = 20,
        *,
        trace_id: str | None = None,
        thread_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or not self.file_path.exists():
            return []

        events = self._read_events()
        result: list[dict[str, Any]] = []
        for event in reversed(events):
            if trace_id is not None and event.get("traceId") != trace_id:
                continue
            if thread_id is not None and event.get("threadId") != thread_id:
                continue
            if agent_name is not None and event.get("agentName") != agent_name:
                continue
            result.append(event)
            if len(result) >= limit:
                break
        return result

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.file_path.exists():
            return []

        events: list[dict[str, Any]] = []
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
        return events
