"""Estimated prompt/resource usage telemetry for btwin runtime boundaries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def estimate_tokens(text: object) -> int:
    """Cheap local token estimate for trend tracking when provider usage is unavailable."""
    value = str(text or "")
    if not value:
        return 0
    return max(1, (len(value) + 3) // 4)


class ResourceUsageTelemetryStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "logs" / "resource-usage.jsonl"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def record_prompt(
        self,
        *,
        thread_id: str,
        agent_name: str | None,
        phase: str | None,
        prompt: str,
        response_text: str | None = None,
        context_sections: dict[str, object] | None = None,
        prompt_source: str = "runtime_prompt",
        truncated: bool = False,
        provider_usage: dict[str, object] | None = None,
        schema_version: str = "v1",
    ) -> dict[str, Any]:
        sections = {
            name: {
                "chars": len(str(content or "")),
                "estimated_tokens": estimate_tokens(content),
            }
            for name, content in (context_sections or {}).items()
        }
        input_tokens = estimate_tokens(prompt)
        output_tokens = estimate_tokens(response_text or "")
        event: dict[str, Any] = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "event_type": "resource.prompt.estimated",
            "thread_id": thread_id,
            "agent_name": agent_name,
            "phase": phase,
            "prompt_source": prompt_source,
            "prompt_chars": len(prompt or ""),
            "response_chars": len(response_text or ""),
            "estimated_input_tokens": input_tokens,
            "estimated_output_tokens": output_tokens,
            "estimated_total_tokens": input_tokens + output_tokens,
            "context_sections": sections,
            "truncated": truncated,
            "provider_usage": dict(provider_usage or {}),
            "schema_version": schema_version,
        }
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def tail(self, limit: int = 20, *, thread_id: str | None = None) -> list[dict[str, Any]]:
        if limit <= 0 or not self.file_path.exists():
            return []

        rows: list[dict[str, Any]] = []
        for event in reversed(self._read_events()):
            if thread_id is not None and event.get("thread_id") != thread_id:
                continue
            rows.append(event)
            if len(rows) >= limit:
                break
        return rows

    def summarize(self, *, thread_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        rows = list(reversed(self.tail(limit=limit, thread_id=thread_id)))
        summary: dict[str, Any] = {
            "event_count": len(rows),
            "total_estimated_input_tokens": sum(int(row.get("estimated_input_tokens") or 0) for row in rows),
            "total_estimated_output_tokens": sum(int(row.get("estimated_output_tokens") or 0) for row in rows),
            "total_estimated_tokens": sum(int(row.get("estimated_total_tokens") or 0) for row in rows),
            "truncated_count": sum(1 for row in rows if row.get("truncated")),
            "by_agent": {},
            "by_phase": {},
            "largest_sections": [],
        }
        section_totals: dict[str, int] = {}
        for row in rows:
            self._add_group(summary["by_agent"], str(row.get("agent_name") or "unknown"), row)
            self._add_group(summary["by_phase"], str(row.get("phase") or "unknown"), row)
            for name, section in dict(row.get("context_sections") or {}).items():
                if isinstance(section, dict):
                    section_totals[name] = section_totals.get(name, 0) + int(section.get("estimated_tokens") or 0)
        summary["largest_sections"] = [
            {"name": name, "estimated_tokens": tokens}
            for name, tokens in sorted(section_totals.items(), key=lambda item: item[1], reverse=True)
        ][:8]
        return summary

    @staticmethod
    def _add_group(groups: dict[str, dict[str, Any]], key: str, row: dict[str, Any]) -> None:
        group = groups.setdefault(
            key,
            {
                "event_count": 0,
                "estimated_input_tokens": 0,
                "estimated_output_tokens": 0,
                "estimated_total_tokens": 0,
                "truncated_count": 0,
            },
        )
        group["event_count"] += 1
        group["estimated_input_tokens"] += int(row.get("estimated_input_tokens") or 0)
        group["estimated_output_tokens"] += int(row.get("estimated_output_tokens") or 0)
        group["estimated_total_tokens"] += int(row.get("estimated_total_tokens") or 0)
        if row.get("truncated"):
            group["truncated_count"] += 1

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
