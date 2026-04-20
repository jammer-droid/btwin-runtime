"""Structured validation telemetry store for btwin-owned runtime signals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ValidationTelemetryStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "logs" / "validation-telemetry.jsonl"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        event_type: str,
        *,
        thread_id: str,
        agent_name: str | None = None,
        phase: str | None = None,
        procedure_step: str | None = None,
        gate: str | None = None,
        visibility: str = "internal",
        evidence_level: str = "critical",
        payload: dict[str, Any] | None = None,
        schema_version: str = "v1",
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "thread_id": thread_id,
            "agent_name": agent_name,
            "phase": phase,
            "procedure_step": procedure_step,
            "gate": gate,
            "visibility": visibility,
            "evidence_level": evidence_level,
            "schema_version": schema_version,
            "payload": dict(payload or {}),
        }
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def tail(
        self,
        limit: int = 20,
        *,
        thread_id: str | None = None,
        evidence_level: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or not self.file_path.exists():
            return []

        rows: list[dict[str, Any]] = []
        for event in reversed(self._read_events()):
            if thread_id is not None and event.get("thread_id") != thread_id:
                continue
            if evidence_level is not None and event.get("evidence_level") != evidence_level:
                continue
            rows.append(event)
            if len(rows) >= limit:
                break
        return rows

    def _read_events(self) -> list[dict[str, Any]]:
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
