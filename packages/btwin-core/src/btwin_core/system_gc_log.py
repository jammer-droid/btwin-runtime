"""Store tombstone records for runtime GC events."""

from __future__ import annotations

import json
from pathlib import Path


class SystemGcLog:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "runtime" / "system-gc-log.jsonl"

    def append_event(self, event: dict[str, object]) -> dict[str, object]:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def list_events(self) -> list[dict[str, object]]:
        if not self.file_path.exists():
            return []

        events: list[dict[str, object]] = []
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events
