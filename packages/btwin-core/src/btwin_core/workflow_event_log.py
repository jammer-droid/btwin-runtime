"""Thread-local append-only workflow event log helpers."""

from __future__ import annotations

import json
from pathlib import Path


class WorkflowEventLog:
    """Append and read compact workflow event records from a JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def list_events(self, limit: int | None = None) -> list[dict[str, object]]:
        if not self.path.exists():
            return []

        events: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                events.append(payload)

        if limit is None or limit >= len(events):
            return events
        return events[-limit:]
