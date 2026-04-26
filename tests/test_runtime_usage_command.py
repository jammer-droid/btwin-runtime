import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.resource_usage_telemetry import ResourceUsageTelemetryStore


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def test_runtime_usage_command_filters_by_runtime_session(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = project_root / ".btwin"
    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    store = ResourceUsageTelemetryStore(data_dir)
    store.record_provider_usage(
        runtime_session_id="runtime-session-1",
        thread_id=None,
        agent_name="foreground",
        phase=None,
        provider="codex",
        provider_thread_id="provider-thread-1",
        provider_turn_id="turn-1",
        token_usage={
            "last": {
                "inputTokens": 80,
                "cachedInputTokens": 20,
                "outputTokens": 10,
                "reasoningOutputTokens": 5,
                "totalTokens": 95,
            }
        },
    )
    store.record_provider_usage(
        runtime_session_id="runtime-session-2",
        thread_id="thread-2",
        agent_name="developer",
        phase="implement",
        provider="codex",
        provider_thread_id="provider-thread-2",
        provider_turn_id="turn-2",
        token_usage={
            "last": {
                "inputTokens": 40,
                "cachedInputTokens": 5,
                "outputTokens": 5,
                "reasoningOutputTokens": 0,
                "totalTokens": 45,
            }
        },
    )

    result = runner.invoke(
        app,
        [
            "runtime",
            "usage",
            "--session",
            "runtime-session-1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["event_count"] == 1
    assert payload["summary"]["actual_total_tokens"] == 95
    assert payload["rows"][0]["runtime_session_id"] == "runtime-session-1"
    assert payload["rows"][0]["btwin_thread_id"] is None
