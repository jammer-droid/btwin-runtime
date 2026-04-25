from __future__ import annotations

from pathlib import Path

from btwin_core.resource_usage_telemetry import ResourceUsageTelemetryStore


def test_resource_usage_telemetry_store_records_provider_token_usage(tmp_path: Path) -> None:
    store = ResourceUsageTelemetryStore(tmp_path)

    event = store.record_provider_usage(
        thread_id="thread-1",
        agent_name="developer",
        phase="implement",
        provider="codex",
        provider_thread_id="codex-thread-1",
        provider_turn_id="turn-1",
        prompt_source="context_pack",
        token_usage={
            "last": {
                "inputTokens": 100,
                "cachedInputTokens": 40,
                "outputTokens": 20,
                "reasoningOutputTokens": 5,
                "totalTokens": 120,
            },
            "total": {
                "inputTokens": 300,
                "cachedInputTokens": 160,
                "outputTokens": 60,
                "reasoningOutputTokens": 15,
                "totalTokens": 360,
            },
            "modelContextWindow": 258400,
        },
        context_sections=["context_pack", "phase_contract"],
    )

    assert event["event_type"] == "resource.provider_token_usage"
    assert event["source"] == "codex_app_server"
    assert event["provider_usage"]["last"]["totalTokens"] == 120
    assert event["actual_total_tokens"] == 120
    assert event["actual_uncached_input_tokens"] == 60
    assert event["actual_cache_hit_ratio"] == 0.4
    assert event["actual_reasoning_ratio"] == 5 / 120
    assert event["provider_thread_id"] == "codex-thread-1"
    assert event["provider_turn_id"] == "turn-1"
    assert event["context_sections"] == ["context_pack", "phase_contract"]


def test_resource_usage_telemetry_summarizes_provider_usage_by_agent_and_phase(tmp_path: Path) -> None:
    store = ResourceUsageTelemetryStore(tmp_path)
    store.record_provider_usage(
        thread_id="thread-1",
        agent_name="developer",
        phase="implement",
        provider="codex",
        provider_thread_id="codex-thread-1",
        provider_turn_id="turn-1",
        token_usage={
            "last": {
                "inputTokens": 100,
                "cachedInputTokens": 25,
                "outputTokens": 20,
                "reasoningOutputTokens": 5,
                "totalTokens": 120,
            }
        },
    )
    store.record_provider_usage(
        thread_id="thread-1",
        agent_name="reviewer",
        phase="review",
        provider="codex",
        provider_thread_id="codex-thread-2",
        provider_turn_id="turn-2",
        token_usage={
            "last": {
                "inputTokens": 200,
                "cachedInputTokens": 100,
                "outputTokens": 40,
                "reasoningOutputTokens": 20,
                "totalTokens": 240,
            }
        },
    )

    summary = store.summarize_provider_usage(thread_id="thread-1")

    assert summary["event_count"] == 2
    assert summary["actual_total_tokens"] == 360
    assert summary["actual_uncached_input_tokens"] == 175
    assert summary["by_agent"]["developer"]["actual_total_tokens"] == 120
    assert summary["by_agent"]["reviewer"]["actual_reasoning_output_tokens"] == 20
    assert summary["by_phase"]["review"]["actual_uncached_input_tokens"] == 100
    assert summary["hotspots"][0]["agent_name"] == "reviewer"
