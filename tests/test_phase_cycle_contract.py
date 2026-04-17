import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolSection, ProtocolStore
from btwin_core.runtime_binding_store import RuntimeBindingStore
from btwin_core.thread_store import ThreadStore
from btwin_core.workflow_event_log import WorkflowEventLog


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _seed_close_context(tmp_path: Path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(
        Protocol(
            name="close-next",
            description="Single phase that closes after one contribution",
            phases=[
                ProtocolPhase(
                    name="summary",
                    actions=["contribute"],
                    template=[
                        ProtocolSection(section="completed", required=True),
                        ProtocolSection(section="remaining", required=True),
                    ],
                )
            ],
        )
    )
    thread = thread_store.create_thread(
        topic="Phase cycle contract",
        protocol="close-next",
        participants=["alice"],
        initial_phase="summary",
    )
    return project_root, data_dir, thread_store, thread


def _system_mailbox_path(project_root: Path) -> Path:
    return project_root / ".btwin" / "runtime" / "system-mailbox.jsonl"


def _load_mailbox_reports(project_root: Path) -> list[dict[str, object]]:
    path = _system_mailbox_path(project_root)
    if not path.exists():
        return []
    reports: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            reports.append(payload)
    return reports


def test_stop_hook_block_does_not_finish_cycle(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_close_context(tmp_path)
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    payload = {
        "session_id": "codex-session-1",
        "cwd": str(project_root),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    events = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    assert events[-1]["event_type"] == "phase_exit_blocked"
    assert events[-1]["scope"] == "local_recovery"
    assert events[-1]["cycle_finished"] is False
    assert _load_mailbox_reports(project_root) == []


def test_cycle_gate_completion_creates_mailbox_report(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_close_context(tmp_path)
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "summary",
        content="## completed\nDone.\n\n## remaining\nNone.\n",
        tldr="summary ready",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--thread",
            thread["thread_id"],
            "--summary",
            "Work complete",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    reports = _load_mailbox_reports(project_root)
    assert len(reports) == 1
    assert reports[0]["thread_id"] == thread["thread_id"]
    assert reports[0]["report_type"] == "cycle_result"
    assert reports[0]["audience"] == "monitoring"
    assert reports[0]["cycle_finished"] is True
    assert reports[0]["source_action"] == "close_thread"
    events = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    assert events[-1]["event_type"] == "cycle_gate_completed"
    assert events[-1]["scope"] == "cycle_gate"
    assert events[-1]["cycle_finished"] is True


def test_cycle_gate_event_records_previous_and_next_cycle_index(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(
        Protocol(
            name="review-retry",
            description="Repeat review until accepted.",
            phases=[
                ProtocolPhase(
                    name="review",
                    description="Review and revise the work.",
                    actions=["contribute"],
                    template=[ProtocolSection(section="completed", required=True)],
                    procedure=[
                        {"role": "reviewer", "action": "review", "guidance": "Review the current implementation state."},
                        {"role": "implementer", "action": "revise", "guidance": "Implement revisions from review feedback."},
                    ],
                )
            ],
            transitions=[
                {"from": "review", "to": "review", "on": "retry"},
            ],
            outcomes=["retry", "accept"],
        )
    )
    thread = thread_store.create_thread(
        topic="Cycle event metadata",
        protocol="review-retry",
        participants=["alice"],
        initial_phase="review",
    )
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "review",
        content="## completed\nNeeds another pass.\n",
        tldr="retry once",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--outcome",
            "retry",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    events = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    assert events[-1]["event_type"] == "cycle_gate_completed"
    assert events[-1]["cycle_index"] == 1
    assert events[-1]["next_cycle_index"] == 2
    reports = _load_mailbox_reports(project_root)
    assert reports[-1]["summary"] == "Phase `review` requested retry; continuing in `review` with active cycle 2."
