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
            "event_type": "hook_decision",
            "hook_event_name": "Stop",
            "decision": "block",
            "reason": "missing_contribution",
            "summary": "Stop blocked until alice contributes.",
        }
    )
    log.append(
        {
            "timestamp": "2026-04-15T01:09:23+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "phase": "context",
            "event_type": "contribution_recorded",
            "summary": "shared context",
        }
    )
    return project_root, data_dir, thread_store, thread


def test_thread_watch_renders_status_header_and_recent_events(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_thread(tmp_path)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(app, ["thread", "watch", thread["thread_id"]])

    assert result.exit_code == 0, result.output
    assert thread["thread_id"] in result.output
    assert "phase=context" in result.output
    assert "alice=contributed" in result.output
    assert "Stop blocked until alice contributes." in result.output
    assert "shared context" in result.output
