import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolSection, ProtocolStore
from btwin_core.thread_store import ThreadStore
from btwin_core.workflow_event_log import WorkflowEventLog


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _seed_thread(tmp_path: Path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(
        Protocol(
            name="workflow-watch",
            description="Watch command protocol",
            phases=[
                ProtocolPhase(
                    name="context",
                    actions=["contribute"],
                    template=[ProtocolSection(section="background", required=True)],
                )
            ],
        )
    )
    thread = thread_store.create_thread(
        topic="Workflow watch",
        protocol="workflow-watch",
        participants=["alice"],
        initial_phase="context",
    )
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "context",
        content="## background\nKnown context.\n",
        tldr="shared context",
    )
    log = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"]))
    log.append(
        {
            "timestamp": "2026-04-15T01:09:10+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "phase": "context",
            "event_type": "phase_exit_check_requested",
            "source": "codex.hook",
            "hook_event_name": "Stop",
            "summary": "Stop exit check requested.",
        }
    )
    log.append(
        {
            "timestamp": "2026-04-15T01:09:23+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "phase": "context",
            "event_type": "required_result_recorded",
            "source": "btwin.contribution.submit",
            "summary": "shared context",
        }
    )
    log.append(
        {
            "timestamp": "2026-04-15T01:09:45+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "event_type": "runtime_binding_closed",
            "source": "btwin.runtime.binding.cleanup",
            "reason": "stale_last_seen",
            "summary": "Runtime binding closed: stale last seen.",
        }
    )
    return project_root, data_dir, thread_store, thread


def test_thread_watch_renders_status_header_and_recent_events(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_thread(tmp_path)
    WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).append(
        {
            "timestamp": "2026-04-15T01:10:02+00:00",
            "thread_id": thread["thread_id"],
            "phase": "context",
            "event_type": "cycle_gate_completed",
            "source": "btwin.protocol.apply_next",
            "cycle_index": 1,
            "next_cycle_index": 2,
            "summary": "Phase `context` requested retry; continuing in `context` with active cycle 2.",
        }
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(app, ["thread", "watch", thread["thread_id"]])

    assert result.exit_code == 0, result.output
    assert thread["thread_id"] in result.output
    assert "phase=context" in result.output
    assert "alice=contributed" in result.output
    assert "Stop exit check requested." in result.output
    assert "Required result recorded" in result.output
    assert "Runtime binding closed" in result.output
    assert "shared context" in result.output
    assert "Cycle gate completed" in result.output
    assert "cycle: 1 -> 2" in result.output


def test_thread_watch_json_emits_normalized_trace_rows(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_thread(tmp_path)
    WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).append(
        {
            "timestamp": "2026-04-15T01:10:02+00:00",
            "thread_id": thread["thread_id"],
            "phase": "context",
            "event_type": "cycle_gate_completed",
            "source": "btwin.protocol.apply_next",
            "cycle_index": 1,
            "next_cycle_index": 2,
            "summary": "Phase `context` requested retry; continuing in `context` with active cycle 2.",
        }
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(app, ["thread", "watch", thread["thread_id"], "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["thread_id"] == thread["thread_id"]
    assert payload["protocol"] == "workflow-watch"
    assert payload["current_phase"] == "context"
    assert payload["topic"] == "Workflow watch"
    assert payload["status_summary"]["agents"][0]["name"] == "alice"
    assert len(payload["trace"]) == 4

    required_fields = {
        "kind",
        "timestamp",
        "thread_id",
        "phase",
        "cycle_index",
        "next_cycle_index",
        "outcome",
        "procedure_key",
        "procedure_alias",
        "gate_key",
        "gate_alias",
        "target_phase",
        "reason",
        "summary",
        "source",
    }
    for row in payload["trace"]:
        assert required_fields.issubset(row)

    first = payload["trace"][0]
    assert first["kind"] == "phase_exit_check"
    assert first["timestamp"] == "2026-04-15T01:09:10+00:00"
    assert first["thread_id"] == thread["thread_id"]
    assert first["phase"] == "context"
    assert first["cycle_index"] is None
    assert first["next_cycle_index"] is None
    assert first["outcome"] is None
    assert first["procedure_key"] is None
    assert first["procedure_alias"] is None
    assert first["gate_key"] is None
    assert first["gate_alias"] is None
    assert first["target_phase"] is None
    assert first["reason"] is None
    assert first["summary"] == "Stop exit check requested."
    assert first["source"] == "codex.hook"

    last = payload["trace"][-1]
    assert last["kind"] == "cycle_gate"
    assert last["timestamp"] == "2026-04-15T01:10:02+00:00"
    assert last["thread_id"] == thread["thread_id"]
    assert last["phase"] == "context"
    assert last["cycle_index"] == 1
    assert last["next_cycle_index"] == 2
    assert last["outcome"] is None
    assert last["procedure_key"] is None
    assert last["procedure_alias"] is None
    assert last["gate_key"] is None
    assert last["gate_alias"] is None
    assert last["target_phase"] is None
    assert last["reason"] is None
    assert last["summary"] == "Phase `context` requested retry; continuing in `context` with active cycle 2."
    assert last["source"] == "btwin.protocol.apply_next"
