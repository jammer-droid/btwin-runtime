"""Provider token usage telemetry for btwin runtime boundaries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UNCACHED_INPUT_RATIO_WARNING_THRESHOLD = 0.5
REASONING_RATIO_WARNING_THRESHOLD = 0.3
TURN_TOTAL_TOKENS_WARNING_THRESHOLD = 50_000


class ResourceUsageTelemetryStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "logs" / "resource-usage.jsonl"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def record_provider_usage(
        self,
        *,
        agent_name: str | None,
        phase: str | None,
        provider: str | None,
        provider_thread_id: str | None,
        provider_turn_id: str | None,
        token_usage: dict[str, object],
        thread_id: str | None = None,
        runtime_session_id: str | None = None,
        btwin_thread_id: str | None = None,
        prompt_source: str = "runtime_prompt",
        context_sections: list[str] | tuple[str, ...] | None = None,
        cycle_index: int | None = None,
        source: str = "codex_app_server",
        schema_version: str = "v1",
    ) -> dict[str, Any]:
        last = _coerce_breakdown(token_usage.get("last") if isinstance(token_usage, dict) else None)
        total = _coerce_breakdown(token_usage.get("total") if isinstance(token_usage, dict) else None)
        model_context_window = _coerce_int(token_usage.get("modelContextWindow")) if isinstance(token_usage, dict) else None
        canonical_btwin_thread_id = _coerce_text(btwin_thread_id) or _coerce_text(thread_id)
        canonical_runtime_session_id = (
            _coerce_text(runtime_session_id)
            or _derive_runtime_session_id(
                btwin_thread_id=canonical_btwin_thread_id,
                agent_name=agent_name,
                provider_thread_id=provider_thread_id,
            )
        )
        input_tokens = last["inputTokens"]
        cached_input_tokens = last["cachedInputTokens"]
        uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
        total_tokens = last["totalTokens"]
        cache_hit_ratio = _safe_ratio(cached_input_tokens, input_tokens)
        uncached_input_ratio = _safe_ratio(uncached_input_tokens, input_tokens)
        reasoning_ratio = _safe_ratio(last["reasoningOutputTokens"], total_tokens)
        usage_warnings = _usage_warnings(
            uncached_input_ratio=uncached_input_ratio,
            reasoning_ratio=reasoning_ratio,
            total_tokens=total_tokens,
        )
        event: dict[str, Any] = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "event_type": "resource.provider_token_usage",
            "source": source,
            "runtime_session_id": canonical_runtime_session_id,
            "btwin_thread_id": canonical_btwin_thread_id,
            "thread_id": canonical_btwin_thread_id,
            "agent_name": agent_name,
            "phase": phase,
            "provider": provider,
            "provider_thread_id": provider_thread_id,
            "provider_turn_id": provider_turn_id,
            "cycle_index": cycle_index,
            "prompt_source": prompt_source,
            "actual_input_tokens": input_tokens,
            "actual_cached_input_tokens": cached_input_tokens,
            "actual_uncached_input_tokens": uncached_input_tokens,
            "actual_output_tokens": last["outputTokens"],
            "actual_reasoning_output_tokens": last["reasoningOutputTokens"],
            "actual_total_tokens": total_tokens,
            "actual_cache_hit_ratio": cache_hit_ratio,
            "actual_uncached_input_ratio": uncached_input_ratio,
            "actual_reasoning_ratio": reasoning_ratio,
            "model_context_window": model_context_window,
            "provider_usage": {
                "last": last,
                "total": total,
                "modelContextWindow": model_context_window,
            },
            "context_sections": list(context_sections or []),
            "usage_warnings": usage_warnings,
            "schema_version": schema_version,
        }
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def tail(
        self,
        limit: int = 20,
        *,
        thread_id: str | None = None,
        runtime_session_id: str | None = None,
        provider_thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or not self.file_path.exists():
            return []

        rows: list[dict[str, Any]] = []
        for event in reversed(self._read_events()):
            if thread_id is not None and _event_btwin_thread_id(event) != thread_id:
                continue
            if runtime_session_id is not None and event.get("runtime_session_id") != runtime_session_id:
                continue
            if provider_thread_id is not None and event.get("provider_thread_id") != provider_thread_id:
                continue
            rows.append(event)
            if len(rows) >= limit:
                break
        return rows

    def summarize_provider_usage(
        self,
        *,
        thread_id: str | None = None,
        runtime_session_id: str | None = None,
        provider_thread_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        rows = [
            row
            for row in reversed(
                self.tail(
                    limit=limit,
                    thread_id=thread_id,
                    runtime_session_id=runtime_session_id,
                    provider_thread_id=provider_thread_id,
                )
            )
            if row.get("event_type") == "resource.provider_token_usage"
        ]
        summary: dict[str, Any] = {
            "event_count": len(rows),
            "actual_input_tokens": sum(int(row.get("actual_input_tokens") or 0) for row in rows),
            "actual_cached_input_tokens": sum(int(row.get("actual_cached_input_tokens") or 0) for row in rows),
            "actual_uncached_input_tokens": sum(int(row.get("actual_uncached_input_tokens") or 0) for row in rows),
            "actual_output_tokens": sum(int(row.get("actual_output_tokens") or 0) for row in rows),
            "actual_reasoning_output_tokens": sum(int(row.get("actual_reasoning_output_tokens") or 0) for row in rows),
            "actual_total_tokens": sum(int(row.get("actual_total_tokens") or 0) for row in rows),
            "by_runtime_session": {},
            "by_provider_thread": {},
            "by_agent": {},
            "by_phase": {},
            "by_cycle": {},
            "warning_counts": {},
            "hotspots": [],
        }
        for row in rows:
            self._add_provider_group(
                summary["by_runtime_session"],
                str(row.get("runtime_session_id") or "unknown"),
                row,
            )
            self._add_provider_group(
                summary["by_provider_thread"],
                str(row.get("provider_thread_id") or "unknown"),
                row,
            )
            self._add_provider_group(summary["by_agent"], str(row.get("agent_name") or "unknown"), row)
            self._add_provider_group(summary["by_phase"], str(row.get("phase") or "unknown"), row)
            self._add_provider_group(summary["by_cycle"], str(row.get("cycle_index") or "unknown"), row)
            for warning in row.get("usage_warnings") or []:
                if isinstance(warning, str) and warning:
                    summary["warning_counts"][warning] = int(summary["warning_counts"].get(warning, 0)) + 1
        summary["hotspots"] = sorted(
            rows,
            key=lambda item: int(item.get("actual_total_tokens") or 0),
            reverse=True,
        )[:5]
        return summary

    @staticmethod
    def _add_provider_group(groups: dict[str, dict[str, Any]], key: str, row: dict[str, Any]) -> None:
        group = groups.setdefault(
            key,
            {
                "event_count": 0,
                "actual_input_tokens": 0,
                "actual_cached_input_tokens": 0,
                "actual_uncached_input_tokens": 0,
                "actual_output_tokens": 0,
                "actual_reasoning_output_tokens": 0,
                "actual_total_tokens": 0,
                "max_turn_tokens": 0,
            },
        )
        group["event_count"] += 1
        for field in (
            "actual_input_tokens",
            "actual_cached_input_tokens",
            "actual_uncached_input_tokens",
            "actual_output_tokens",
            "actual_reasoning_output_tokens",
            "actual_total_tokens",
        ):
            group[field] += int(row.get(field) or 0)
        group["max_turn_tokens"] = max(group["max_turn_tokens"], int(row.get("actual_total_tokens") or 0))

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


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_breakdown(value: object) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {
        "inputTokens": _coerce_int(source.get("inputTokens")) or 0,
        "cachedInputTokens": _coerce_int(source.get("cachedInputTokens")) or 0,
        "outputTokens": _coerce_int(source.get("outputTokens")) or 0,
        "reasoningOutputTokens": _coerce_int(source.get("reasoningOutputTokens")) or 0,
        "totalTokens": _coerce_int(source.get("totalTokens")) or 0,
    }


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _usage_warnings(
    *,
    uncached_input_ratio: float,
    reasoning_ratio: float,
    total_tokens: int,
) -> list[str]:
    warnings: list[str] = []
    if uncached_input_ratio >= UNCACHED_INPUT_RATIO_WARNING_THRESHOLD:
        warnings.append("uncached_input_ratio_high")
    if reasoning_ratio >= REASONING_RATIO_WARNING_THRESHOLD:
        warnings.append("reasoning_ratio_high")
    if total_tokens >= TURN_TOTAL_TOKENS_WARNING_THRESHOLD:
        warnings.append("turn_total_tokens_high")
    return warnings


def _coerce_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _derive_runtime_session_id(
    *,
    btwin_thread_id: str | None,
    agent_name: str | None,
    provider_thread_id: str | None,
) -> str | None:
    if btwin_thread_id and agent_name:
        return f"{btwin_thread_id}:{agent_name}"
    return _coerce_text(provider_thread_id)


def _event_btwin_thread_id(event: dict[str, Any]) -> object:
    return event.get("btwin_thread_id") or event.get("thread_id")
