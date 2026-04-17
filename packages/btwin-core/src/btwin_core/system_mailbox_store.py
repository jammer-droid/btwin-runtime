"""Store cycle-level mailbox reports separate from agent inbox state."""

from __future__ import annotations

import json
from pathlib import Path


class SystemMailboxStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "runtime" / "system-mailbox.jsonl"

    def append_report(self, report: dict[str, object]) -> dict[str, object]:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False) + "\n")
        return report

    def list_reports(
        self,
        *,
        thread_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        if not self.file_path.exists():
            return []

        reports: list[dict[str, object]] = []
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if thread_id is not None and payload.get("thread_id") != thread_id:
                continue
            reports.append(payload)

        reports.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        if limit is None or limit >= len(reports):
            return reports
        return reports[:limit]

    def delete_reports_for_threads(self, thread_ids: set[str]) -> int:
        if not thread_ids or not self.file_path.exists():
            return 0

        kept_lines: list[str] = []
        deleted_count = 0
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                kept_lines.append(line)
                continue
            if isinstance(payload, dict) and payload.get("thread_id") in thread_ids:
                deleted_count += 1
                continue
            kept_lines.append(line)

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        rewritten = "\n".join(kept_lines)
        if rewritten:
            rewritten += "\n"
        self.file_path.write_text(rewritten, encoding="utf-8")
        return deleted_count
