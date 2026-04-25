from __future__ import annotations

from pathlib import Path

from btwin_core.resource_usage_telemetry import ResourceUsageTelemetryStore


def test_resource_usage_telemetry_store_records_prompt_estimates(tmp_path: Path) -> None:
    store = ResourceUsageTelemetryStore(tmp_path)

    event = store.record_prompt(
        thread_id="thread-1",
        agent_name="developer",
        phase="implement",
        prompt="hello world " * 20,
        response_text="done",
        context_sections={
            "control": "phase=implement",
            "phase_contract": "## implementation",
        },
        prompt_source="context_pack",
        truncated=False,
    )

    assert event["event_type"] == "resource.prompt.estimated"
    assert event["thread_id"] == "thread-1"
    assert event["agent_name"] == "developer"
    assert event["phase"] == "implement"
    assert event["prompt_chars"] == len("hello world " * 20)
    assert event["estimated_input_tokens"] > 0
    assert event["estimated_output_tokens"] > 0
    assert event["context_sections"]["control"]["chars"] == len("phase=implement")
    assert event["prompt_source"] == "context_pack"
    assert event["truncated"] is False
    assert store.file_path == tmp_path / "logs" / "resource-usage.jsonl"


def test_resource_usage_telemetry_store_summarizes_by_thread(tmp_path: Path) -> None:
    store = ResourceUsageTelemetryStore(tmp_path)
    store.record_prompt(
        thread_id="thread-1",
        agent_name="developer",
        phase="implement",
        prompt="a" * 80,
        response_text="b" * 20,
        context_sections={"control": "a" * 40},
    )
    store.record_prompt(
        thread_id="thread-1",
        agent_name="reviewer",
        phase="review",
        prompt="c" * 40,
        response_text="d" * 12,
        context_sections={"phase_contract": "c" * 20},
        truncated=True,
    )
    store.record_prompt(
        thread_id="thread-2",
        agent_name="developer",
        phase="implement",
        prompt="e" * 120,
        response_text="",
        context_sections={},
    )

    rows = store.tail(limit=10, thread_id="thread-1")
    summary = store.summarize(thread_id="thread-1")

    assert len(rows) == 2
    assert summary["event_count"] == 2
    assert summary["total_estimated_tokens"] == sum(row["estimated_total_tokens"] for row in rows)
    assert summary["by_agent"]["developer"]["event_count"] == 1
    assert summary["by_agent"]["reviewer"]["event_count"] == 1
    assert summary["by_phase"]["implement"]["event_count"] == 1
    assert summary["by_phase"]["review"]["truncated_count"] == 1
    assert summary["largest_sections"][0]["name"] == "control"
