import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_store import PhaseCycleStore
from btwin_core.protocol_store import (
    Protocol,
    ProtocolPhase,
    ProtocolSection,
    ProtocolStore,
    ProtocolTransition,
    compile_protocol_definition,
)
from btwin_core.runtime_binding_store import RuntimeBindingStore
from btwin_core.thread_store import ThreadStore
from btwin_core.workflow_event_log import WorkflowEventLog
from tests.protocol_scenario_matrix import scenario_protocol_definition


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


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


def _seed_retry_trace_thread(tmp_path: Path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(
        Protocol(
            name="review-loop",
            phases=[
                ProtocolPhase(
                    name="review",
                    actions=["contribute"],
                    template=[ProtocolSection(section="completed", required=True)],
                    procedure=[
                        {"role": "reviewer", "action": "review", "alias": "Review", "key": "review-pass"},
                        {"role": "implementer", "action": "revise", "alias": "Revise", "key": "revise-pass"},
                    ],
                ),
                ProtocolPhase(name="decision", actions=["decide"]),
            ],
            transitions=[
                ProtocolTransition.model_validate(
                    {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "retry-loop"}
                ),
                ProtocolTransition.model_validate(
                    {"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate", "key": "accept-gate"}
                ),
            ],
            outcomes=["retry", "accept"],
        )
    )
    thread = thread_store.create_thread(
        topic="Retry trace",
        protocol="review-loop",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(project_root / ".btwin").write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review", "revise"],
        ).model_copy(
            update={
                "cycle_index": 2,
                "current_step_label": "review",
                "last_gate_outcome": "retry",
            }
        )
    )
    WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).append(
        {
            "timestamp": "2026-04-15T01:10:02+00:00",
            "thread_id": thread["thread_id"],
            "phase": "review",
            "event_type": "cycle_gate_completed",
            "source": "btwin.protocol.apply_next",
            "cycle_index": 1,
            "next_cycle_index": 2,
            "summary": "Phase `review` requested retry; continuing in `review` with active cycle 2.",
        }
    )
    mailbox_path = project_root / ".btwin" / "runtime" / "system-mailbox.jsonl"
    mailbox_path.parent.mkdir(parents=True, exist_ok=True)
    mailbox_path.write_text(
        json.dumps(
            {
                "thread_id": thread["thread_id"],
                "report_type": "cycle_result",
                "audience": "monitoring",
                "summary": "Phase `review` requested retry; continuing in `review` with active cycle 2.",
                "cycle_finished": True,
                "created_at": "2026-04-15T01:10:02+00:00",
                "phase": "review",
                "protocol": "review-loop",
                "next_phase": "review",
                "cycle_index": 1,
                "next_cycle_index": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return project_root, data_dir, thread_store, thread


def _seed_traceable_review_thread(tmp_path: Path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(
        Protocol(
            name="review-loop",
            phases=[
                ProtocolPhase(
                    name="review",
                    actions=["contribute"],
                    template=[ProtocolSection(section="completed", required=True)],
                    procedure=[
                        {"role": "reviewer", "action": "review", "alias": "Review", "key": "review-pass"},
                        {"role": "implementer", "action": "revise", "alias": "Revise", "key": "revise-pass"},
                    ],
                ),
                ProtocolPhase(name="decision", actions=["decide"]),
            ],
            transitions=[
                ProtocolTransition.model_validate(
                    {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "retry-loop"}
                ),
                ProtocolTransition.model_validate(
                    {"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate", "key": "accept-gate"}
                ),
            ],
            outcomes=["retry", "accept"],
        )
    )
    thread = thread_store.create_thread(
        topic="Traceable review",
        protocol="review-loop",
        participants=["alice"],
        initial_phase="review",
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
        "outcome_policy",
        "outcome_emitters",
        "outcome_actions",
        "policy_outcomes",
    }
    for row in payload["trace"]:
        assert required_fields.issubset(row)

    first = payload["trace"][0]
    assert first["kind"] == "guard"
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
    assert first["outcome_policy"] is None
    assert first["outcome_emitters"] == []
    assert first["outcome_actions"] == []
    assert first["policy_outcomes"] == []

    last = payload["trace"][-1]
    assert last["kind"] == "gate"
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
    assert last["outcome_policy"] is None
    assert last["outcome_emitters"] == []
    assert last["outcome_actions"] == []
    assert last["policy_outcomes"] == []


def test_thread_watch_help_describes_timeline_not_hud():
    result = runner.invoke(app, ["thread", "watch", "--help"])

    assert result.exit_code == 0, result.output
    assert "timeline" in result.output.lower()
    assert "hud" not in result.output.lower()


def test_thread_watch_json_derives_gate_semantics_from_existing_runtime_state(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_retry_trace_thread(tmp_path)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(app, ["thread", "watch", thread["thread_id"], "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    row = payload["trace"][-1]

    assert row["kind"] == "gate"
    assert row["phase"] == "review"
    assert row["cycle_index"] == 1
    assert row["next_cycle_index"] == 2
    assert row["procedure_key"] == "review-pass"
    assert row["procedure_alias"] == "Review"
    assert row["gate_key"] == "retry-loop"
    assert row["gate_alias"] == "Retry Gate"
    assert row["outcome"] == "retry"
    assert row["target_phase"] == "review"


def test_thread_watch_json_attached_mode_loads_custom_protocol_from_runtime_api(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_data_dir = project_root / ".btwin"
    shared_data_dir = tmp_path / "shared-btwin"
    project_root.mkdir()

    WorkflowEventLog(shared_data_dir / "threads" / "thread-1" / "workflow-events.jsonl").append(
        {
            "timestamp": "2026-04-15T01:10:02+00:00",
            "thread_id": "thread-1",
            "phase": "review",
            "event_type": "cycle_gate_completed",
            "source": "btwin.protocol.apply_next",
            "cycle_index": 1,
            "next_cycle_index": 2,
            "summary": "Phase `review` requested retry; continuing in `review` with active cycle 2.",
        }
    )

    protocol_calls = []

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached retry trace",
                "protocol": "custom-review",
                "current_phase": "review",
            }
        if path == "/api/threads/thread-1/status":
            return {
                "thread_id": "thread-1",
                "current_phase": "review",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        if path == "/api/threads/thread-1/phase-cycle":
            return {
                "state": {
                    "thread_id": "thread-1",
                    "phase_name": "review",
                    "cycle_index": 2,
                    "procedure_steps": ["review", "revise"],
                    "current_step_label": "review",
                    "last_gate_outcome": "retry",
                    "status": "active",
                },
                "visual": {
                    "procedure": [
                        {"key": "review-pass", "label": "Review", "status": "active"},
                        {"key": "revise-pass", "label": "Revise", "status": "pending"},
                        {"key": "gate", "label": "Gate", "status": "pending"},
                    ],
                    "gates": [
                        {"key": "retry-loop", "label": "Retry Gate", "status": "completed", "target_phase": "review"},
                        {"key": "accept-gate", "label": "Accept Gate", "status": "pending", "target_phase": "decision"},
                    ],
                },
            }
        if path == "/api/system-mailbox":
            assert params == {"threadId": "thread-1", "limit": 5}
            return {
                "count": 1,
                "reports": [
                    {
                        "thread_id": "thread-1",
                        "report_type": "cycle_result",
                        "audience": "monitoring",
                        "summary": "Phase `review` requested retry; continuing in `review` with active cycle 2.",
                        "created_at": "2026-04-15T01:10:02+00:00",
                        "phase": "review",
                        "protocol": "custom-review",
                        "next_phase": "review",
                        "cycle_index": 1,
                        "next_cycle_index": 2,
                    }
                ],
            }
        if path == "/api/protocols/custom-review":
            protocol_calls.append(path)
            payload = scenario_protocol_definition("retry_same_phase")
            payload["name"] = "custom-review"
            return payload
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(project_data_dir))
    monkeypatch.setattr(main, "_service_data_dir", lambda: shared_data_dir)
    monkeypatch.setattr(main, "_api_get", fake_api_get)

    result = runner.invoke(app, ["thread", "watch", "thread-1", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    row = payload["trace"][-1]

    assert protocol_calls == ["/api/protocols/custom-review"]
    assert row["procedure_key"] == "review-pass"
    assert row["procedure_alias"] == "Review"
    assert row["gate_key"] == "retry-loop"
    assert row["gate_alias"] == "Retry Gate"
    assert row["outcome"] == "retry"
    assert row["target_phase"] == "review"
    assert row["outcome_policy"] == "review-outcomes"
    assert row["outcome_emitters"] == ["reviewer", "user"]
    assert row["outcome_actions"] == ["decide"]
    assert row["policy_outcomes"] == ["retry", "accept", "close"]


def test_thread_watch_json_attached_mode_synthesizes_gate_row_without_gate_event_or_mailbox(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_data_dir = project_root / ".btwin"
    shared_data_dir = tmp_path / "shared-btwin"
    project_root.mkdir()

    WorkflowEventLog(shared_data_dir / "threads" / "thread-1" / "workflow-events.jsonl").append(
        {
            "timestamp": "2026-04-15T01:09:23+00:00",
            "thread_id": "thread-1",
            "phase": "review",
            "event_type": "required_result_recorded",
            "source": "btwin.contribution.submit",
            "summary": "review contribution recorded",
        }
    )

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached retry trace",
                "protocol": "custom-review",
                "current_phase": "review",
            }
        if path == "/api/threads/thread-1/status":
            return {
                "thread_id": "thread-1",
                "current_phase": "review",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        if path == "/api/threads/thread-1/phase-cycle":
            return {
                "state": {
                    "thread_id": "thread-1",
                    "phase_name": "review",
                    "cycle_index": 2,
                    "procedure_steps": ["review", "revise"],
                    "current_step_label": "review",
                    "last_gate_outcome": "retry",
                    "last_completed_at": "2026-04-15T01:10:02+00:00",
                    "status": "active",
                },
                "visual": {
                    "procedure": [
                        {"key": "review-pass", "label": "Review", "status": "active"},
                        {"key": "revise-pass", "label": "Revise", "status": "pending"},
                        {"key": "gate", "label": "Gate", "status": "pending"},
                    ],
                    "gates": [
                        {"key": "retry-loop", "label": "Retry Gate", "status": "completed", "target_phase": "review"},
                        {"key": "accept-gate", "label": "Accept Gate", "status": "pending", "target_phase": "decision"},
                    ],
                },
            }
        if path == "/api/system-mailbox":
            assert params == {"threadId": "thread-1", "limit": 5}
            return {"count": 0, "reports": []}
        if path == "/api/protocols/custom-review":
            return {
                "name": "custom-review",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review", "key": "review-pass"},
                            {"role": "implementer", "action": "revise", "alias": "Revise", "key": "revise-pass"},
                        ],
                    },
                    {"name": "decision", "actions": ["decide"]},
                ],
                "transitions": [
                    {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "retry-loop"},
                    {"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate", "key": "accept-gate"},
                ],
                "outcomes": ["retry", "accept"],
            }
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(project_data_dir))
    monkeypatch.setattr(main, "_service_data_dir", lambda: shared_data_dir)
    monkeypatch.setattr(main, "_api_get", fake_api_get)

    result = runner.invoke(app, ["thread", "watch", "thread-1", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [row["kind"] for row in payload["trace"]] == ["result", "gate"]

    row = payload["trace"][-1]
    assert row["timestamp"] == "2026-04-15T01:10:02+00:00"
    assert row["source"] == "btwin.thread_watch.synthetic"
    assert row["phase"] == "review"
    assert row["cycle_index"] == 1
    assert row["next_cycle_index"] == 2
    assert row["procedure_key"] == "review-pass"
    assert row["procedure_alias"] == "Review"
    assert row["gate_key"] == "retry-loop"
    assert row["gate_alias"] == "Retry Gate"
    assert row["outcome"] == "retry"
    assert row["target_phase"] == "review"


def test_thread_watch_json_attached_mode_synthesizes_cross_phase_gate_row_without_gate_event_or_mailbox(
    tmp_path, monkeypatch
):
    project_root = tmp_path / "project"
    project_data_dir = project_root / ".btwin"
    shared_data_dir = tmp_path / "shared-btwin"
    project_root.mkdir()

    WorkflowEventLog(shared_data_dir / "threads" / "thread-1" / "workflow-events.jsonl").append(
        {
            "timestamp": "2026-04-15T01:09:23+00:00",
            "thread_id": "thread-1",
            "phase": "review",
            "event_type": "required_result_recorded",
            "source": "btwin.contribution.submit",
            "summary": "review contribution recorded",
        }
    )

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached accept trace",
                "protocol": "custom-review",
                "current_phase": "decision",
            }
        if path == "/api/threads/thread-1/status":
            return {
                "thread_id": "thread-1",
                "current_phase": "decision",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        if path == "/api/threads/thread-1/phase-cycle":
            return {
                "state": {
                    "thread_id": "thread-1",
                    "phase_name": "decision",
                    "cycle_index": 1,
                    "procedure_steps": [],
                    "current_step_label": None,
                    "last_gate_outcome": None,
                    "last_cycle_outcome": "accept",
                    "last_completed_at": "2026-04-15T01:10:02+00:00",
                    "status": "active",
                },
                "visual": {
                    "procedure": [{"key": "gate", "label": "Gate", "status": "pending"}],
                    "gates": [],
                },
            }
        if path == "/api/system-mailbox":
            assert params == {"threadId": "thread-1", "limit": 5}
            return {"count": 0, "reports": []}
        if path == "/api/protocols/custom-review":
            return {
                "name": "custom-review",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review", "key": "review-pass"},
                            {"role": "implementer", "action": "revise", "alias": "Revise", "key": "revise-pass"},
                        ],
                    },
                    {"name": "decision", "actions": ["decide"]},
                ],
                "transitions": [
                    {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "retry-loop"},
                    {"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate", "key": "accept-gate"},
                ],
                "outcomes": ["retry", "accept"],
            }
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(project_data_dir))
    monkeypatch.setattr(main, "_service_data_dir", lambda: shared_data_dir)
    monkeypatch.setattr(main, "_api_get", fake_api_get)

    result = runner.invoke(app, ["thread", "watch", "thread-1", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [row["kind"] for row in payload["trace"]] == ["result", "gate"]

    row = payload["trace"][-1]
    assert row["timestamp"] == "2026-04-15T01:10:02+00:00"
    assert row["source"] == "btwin.thread_watch.synthetic"
    assert row["phase"] == "review"
    assert row["cycle_index"] == 1
    assert row["next_cycle_index"] == 1
    assert row["procedure_key"] == "review-pass"
    assert row["procedure_alias"] == "Review"
    assert row["gate_key"] == "accept-gate"
    assert row["gate_alias"] == "Accept Gate"
    assert row["outcome"] == "accept"
    assert row["target_phase"] == "decision"


def test_thread_watch_records_contribution_event_trace_fields(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_traceable_review_thread(tmp_path)
    PhaseCycleStore(project_root / ".btwin").write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review", "revise"],
        ).model_copy(
            update={
                "cycle_index": 2,
                "current_step_label": "review",
            }
        )
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(
        app,
        [
            "contribution",
            "submit",
            "--thread",
            thread["thread_id"],
            "--agent",
            "alice",
            "--phase",
            "review",
            "--content",
            "## completed\nNeeds another pass.\n",
            "--tldr",
            "review contribution recorded",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    events = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    event = events[-1]
    assert event["event_type"] == "required_result_recorded"
    assert event["cycle_index"] == 2
    assert event["procedure_key"] == "review-pass"
    assert event["procedure_alias"] == "Review"


def test_thread_watch_records_blocked_stop_guard_identity_and_trace_fields(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_traceable_review_thread(tmp_path)
    PhaseCycleStore(project_root / ".btwin").write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review", "revise"],
        )
    )
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
    blocked = events[-1]
    assert blocked["event_type"] == "phase_exit_blocked"
    assert blocked["baseline_guard"] == "contribution_required"
    assert blocked["cycle_index"] == 1
    assert blocked["procedure_key"] == "review-pass"
    assert blocked["procedure_alias"] == "Review"

    watch = runner.invoke(app, ["thread", "watch", thread["thread_id"], "--json"])
    assert watch.exit_code == 0, watch.output
    payload = json.loads(watch.output)
    row = payload["trace"][-1]
    assert row["kind"] == "guard"
    assert row["baseline_guard"] == "contribution_required"


def test_thread_watch_records_apply_next_event_trace_fields(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_traceable_review_thread(tmp_path)
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(compile_protocol_definition(scenario_protocol_definition("retry_same_phase")))
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "review",
        content="## completed\nNeeds another pass.\n",
        tldr="review contribution recorded",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--thread",
            thread["thread_id"],
            "--outcome",
            "retry",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    events = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    event = events[-1]
    assert event["event_type"] == "cycle_gate_completed"
    assert event["cycle_index"] == 1
    assert event["next_cycle_index"] == 2
    assert event["outcome"] == "retry"
    assert event["procedure_key"] == "review-pass"
    assert event["procedure_alias"] == "Review"
    assert event["gate_key"] == "retry-loop"
    assert event["gate_alias"] == "Retry Gate"
    assert event["target_phase"] == "review"

    watch = runner.invoke(app, ["thread", "watch", thread["thread_id"], "--json"])
    assert watch.exit_code == 0, watch.output
    payload = json.loads(watch.output)
    row = payload["trace"][-1]
    assert row["kind"] == "gate"
    assert row["outcome_policy"] == "review-outcomes"
    assert row["outcome_emitters"] == ["reviewer", "user"]
    assert row["outcome_actions"] == ["decide"]
    assert row["policy_outcomes"] == ["retry", "accept", "close"]
