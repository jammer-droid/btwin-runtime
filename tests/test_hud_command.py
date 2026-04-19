import io
import json
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.agent_store import AgentStore
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


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def _renderable_to_text(renderable, width: int = 120, height: int = 40) -> str:
    buffer = io.StringIO()
    Console(file=buffer, force_terminal=False, color_system=None, width=width, height=height).print(renderable)
    return buffer.getvalue()


def test_hud_without_binding_shows_runtime_summary(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "B-TWIN HUD" in result.output
    assert "binding=none" in result.output


def test_hud_with_binding_shows_bound_thread_and_recent_events(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    agent_store = AgentStore(data_dir)
    agent_store.register(
        name="alice",
        model="gpt-5",
        alias="alice",
        provider="codex",
        role="implementer",
    )
    thread = thread_store.create_thread(
        topic="HUD thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).append(
        {
            "timestamp": "2026-04-15T02:12:16+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "phase": "context",
            "event_type": "hook_decision",
            "hook_event_name": "Stop",
            "decision": "allow",
            "summary": "Stop allowed.",
        }
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "binding=alice" in result.output
    assert thread["thread_id"] in result.output
    assert "phase=context" in result.output
    assert "Stop allowed." in result.output


def test_hud_keeps_only_compact_latest_event_snapshot(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    thread = thread_store.create_thread(
        topic="HUD compact thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    log = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"]))
    log.append(
        {
            "timestamp": "2026-04-15T02:12:15+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "phase": "context",
            "event_type": "phase_attempt_started",
            "summary": "Older event should stay out of the HUD snapshot.",
        }
    )
    log.append(
        {
            "timestamp": "2026-04-15T02:12:16+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "phase": "context",
            "event_type": "hook_decision",
            "hook_event_name": "Stop",
            "decision": "allow",
            "summary": "Newest event stays visible.",
        }
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "Latest" in result.output
    assert "Newest event stays visible." in result.output
    assert "Older event should stay out of the HUD snapshot." not in result.output


def test_hud_can_render_system_mailbox_reports(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    thread = thread_store.create_thread(
        topic="HUD mailbox thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    mailbox_path = project_root / ".btwin" / "runtime" / "system-mailbox.jsonl"
    mailbox_path.parent.mkdir(parents=True, exist_ok=True)
    mailbox_path.write_text(
        json.dumps(
            {
                "thread_id": thread["thread_id"],
                "report_type": "cycle_result",
                "audience": "monitoring",
                "summary": "Cycle 1 complete.",
                "cycle_finished": True,
                "created_at": "2026-04-17T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "Cycle Feed" in result.output
    assert "cycle_result" not in result.output
    assert "Cycle 1 complete." in result.output


def test_hud_renders_current_protocol_cycle_and_step(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(
        Protocol(
            name="review-loop",
            guard_sets=[
                {
                    "name": "review-default",
                    "guards": [
                        "phase_actor_eligibility",
                        "direct_target_eligibility",
                    ],
                }
            ],
            phases=[
                ProtocolPhase(
                    name="review",
                    actions=["contribute"],
                    template=[ProtocolSection(section="completed", required=True)],
                    guard_set="review-default",
                    procedure=[
                        {"role": "reviewer", "action": "review", "alias": "Review"},
                        {"role": "implementer", "action": "revise", "alias": "Revise"},
                    ],
                ),
                ProtocolPhase(name="decision", actions=["decide"]),
            ],
            transitions=[
                ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate"}),
                ProtocolTransition.model_validate({"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate"}),
            ],
            outcomes=["retry", "accept"],
        )
    )
    thread = thread_store.create_thread(
        topic="HUD progress thread",
        protocol="review-loop",
        participants=["alice"],
        initial_phase="review",
    )
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    PhaseCycleStore(project_root / ".btwin").write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review", "revise"],
        ).model_copy(
            update={
                "cycle_index": 2,
                "current_step_label": "revise",
                "last_gate_outcome": "retry",
            }
        )
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "Protocol Progress" in result.output
    assert "Active cycle: 2" in result.output
    assert "Completed cycles: 1" in result.output
    assert "Procedure" in result.output
    assert "Review" in result.output
    assert "Revise" in result.output
    assert "Retry Gate" in result.output
    assert "Accept Gate" in result.output
    assert "Guards" in result.output
    assert "phase_actor_eligibility" in result.output
    assert "direct_target_eligibility" in result.output
    assert "Current:" not in result.output
    assert "Last gate:" not in result.output


def test_standalone_phase_cycle_payload_prefers_protocol_keys_in_visuals(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "review-loop",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute"],
                        "template": [{"section": "completed", "required": True}],
                        "outcome_policy": "review-outcomes",
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review", "key": "step-review"},
                            {"role": "implementer", "action": "revise", "alias": "Revise", "key": "step-revise"},
                        ],
                    },
                    {"name": "decision", "actions": ["decide"]},
                ],
                "transitions": [
                    {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "gate-retry"},
                    {"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate", "key": "gate-accept"},
                ],
                "outcome_policies": [
                    {
                        "name": "review-outcomes",
                        "emitters": ["reviewer", "user"],
                        "actions": ["decide"],
                        "outcomes": ["retry", "accept"],
                    }
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="HUD progress thread",
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

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    payload = main._phase_cycle_payload_for_thread(thread["thread_id"], thread=thread, config=_standalone_config(data_dir))

    assert payload is not None
    assert payload["context_core"]["outcome_policy"] == "review-outcomes"
    assert payload["context_core"]["outcome_emitters"] == ["reviewer", "user"]
    assert payload["context_core"]["outcome_actions"] == ["decide"]
    assert payload["context_core"]["policy_outcomes"] == ["retry", "accept"]
    assert payload["visual"]["procedure"][0] == {"key": "step-review", "label": "Review", "status": "active"}
    assert payload["visual"]["procedure"][1] == {"key": "step-revise", "label": "Revise", "status": "pending"}
    assert payload["visual"]["gates"][0] == {
        "key": "gate-retry",
        "label": "Retry Gate",
        "status": "completed",
        "target_phase": "review",
    }
    assert payload["visual"]["gates"][1] == {
        "key": "gate-accept",
        "label": "Accept Gate",
        "status": "pending",
        "target_phase": "decision",
    }


def test_standalone_phase_cycle_payload_uses_step_index_for_repeated_actions(tmp_path, monkeypatch):
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
                        {"role": "reviewer", "action": "review", "alias": "Review 1", "key": "step-review-1"},
                        {"role": "implementer", "action": "revise", "alias": "Revise", "key": "step-revise"},
                        {"role": "reviewer", "action": "review", "alias": "Review 2", "key": "step-review-2"},
                    ],
                ),
            ],
            transitions=[
                ProtocolTransition.model_validate(
                    {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "gate-retry"}
                ),
            ],
            outcomes=["retry", "accept"],
        )
    )
    thread = thread_store.create_thread(
        topic="Repeated review HUD progress thread",
        protocol="review-loop",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(project_root / ".btwin").write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review", "revise", "review"],
        ).model_copy(
            update={
                "current_step_index": 2,
                "current_step_label": "review",
                "completed_steps": ["review", "revise"],
            }
        )
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    payload = main._phase_cycle_payload_for_thread(thread["thread_id"], thread=thread, config=_standalone_config(data_dir))

    assert payload is not None
    assert payload["visual"]["procedure"][0] == {
        "key": "step-review-1",
        "label": "Review 1",
        "status": "completed",
    }
    assert payload["visual"]["procedure"][1] == {
        "key": "step-revise",
        "label": "Revise",
        "status": "completed",
    }
    assert payload["visual"]["procedure"][2] == {
        "key": "step-review-2",
        "label": "Review 2",
        "status": "active",
    }


def test_hud_with_closed_binding_shows_closed_status_without_focusing_thread(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    binding_store = RuntimeBindingStore(project_root / ".btwin")
    binding = binding_store.bind("thread-1", "alice")
    binding_store.close_binding(binding, reason="stale_last_seen")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "binding=alice (closed)" in result.output
    assert "thread-1" not in result.output


def test_hud_attached_thread_view_shows_runtime_diagnostics(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached HUD thread",
                "protocol": "debate",
                "current_phase": "context",
            }
        if path == "/api/threads/thread-1/status":
            return {
                "thread_id": "thread-1",
                "current_phase": "context",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        if path == "/api/agent-runtime-status":
            return {
                "agents": {
                    "alice": [
                        {
                            "thread_id": "thread-1",
                            "transport_mode": "resume_invocation_transport",
                            "status": "failed",
                            "fallback_transport_involved": True,
                            "last_transport_error": "live transport timed out after 6.00s of inactivity",
                        }
                    ]
                }
            }
        if path == "/api/runtime/logs":
            assert params == {"threadId": "thread-1", "limit": 3}
            return {
                "events": [
                    {
                        "timestamp": "2026-04-15T04:32:23+00:00",
                        "eventType": "runtime_transport_fallback",
                        "message": "runtime transport fell back",
                        "transportMode": "resume_invocation_transport",
                    }
                ]
            }
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    rendered = main._render_hud("thread-1", limit=5)

    assert "Runtime" in rendered
    assert "[yellow]alice  transport=resume_invocation_transport  surface=exec  kind=short-term[/yellow]" in rendered
    assert (
        "[yellow]       primary=resume_invocation_transport  status=failed  fallback=yes  "
        "degraded=yes  recoverable=no  recovering=no  recovery_attempts=0[/yellow]"
    ) in rendered
    assert "[red]last_error: live transport timed out after 6.00s of inactivity[/red]" in rendered
    assert "[yellow]04:32:23  runtime_transport_fallback  transport=resume_invocation_transport[/yellow]" in rendered
    assert "runtime transport fell back" in rendered


def test_hud_attached_uses_shared_runtime_state_for_binding_and_workflow_events(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    project_data_dir = project_root / ".btwin"
    shared_data_dir = tmp_path / "shared-btwin"
    project_root.mkdir()

    RuntimeBindingStore(shared_data_dir).bind("thread-1", "alice")
    WorkflowEventLog(shared_data_dir / "threads" / "thread-1" / "workflow-events.jsonl").append(
        {
            "timestamp": "2026-04-15T14:41:29+00:00",
            "thread_id": "thread-1",
            "agent": "alice",
            "phase": "context",
            "event_type": "phase_attempt_started",
            "summary": "Current phase: context. Required result type: contribution.",
            "source": "codex.hook",
            "session_id": "shared-session",
        }
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(project_data_dir))
    monkeypatch.setattr(main, "_service_data_dir", lambda: shared_data_dir)

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached HUD thread",
                "protocol": "debate",
                "current_phase": "context",
            }
        if path == "/api/threads/thread-1/status":
            return {
                "thread_id": "thread-1",
                "current_phase": "context",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        if path == "/api/agent-runtime-status":
            return {"agents": {}}
        if path == "/api/runtime/logs":
            return {"events": []}
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "binding=alice" in result.output
    assert "thread-1" in result.output
    assert "shared-session" in result.output
    assert "Phase attempt started" in result.output


def test_hud_attached_renders_system_mailbox_reports(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached HUD thread",
                "protocol": "debate",
                "current_phase": "context",
            }
        if path == "/api/threads/thread-1/status":
            return {
                "thread_id": "thread-1",
                "current_phase": "context",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        if path == "/api/agent-runtime-status":
            return {"agents": {}}
        if path == "/api/runtime/logs":
            return {"events": []}
        if path == "/api/system-mailbox":
            assert params == {"threadId": "thread-1", "limit": 5}
            return {
                "count": 1,
                "reports": [
                    {
                        "thread_id": "thread-1",
                        "report_type": "cycle_result",
                        "audience": "monitoring",
                        "summary": "Cycle 2 complete.",
                        "created_at": "2026-04-17T01:00:00+00:00",
                    }
                ],
            }
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    rendered = main._render_hud("thread-1", limit=5)

    assert "Cycle Feed" in rendered
    assert "cycle_result" not in rendered
    assert "Cycle 2 complete." in rendered


def test_hud_attached_renders_protocol_progress(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached HUD thread",
                "protocol": "debate",
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
                    "current_step_label": "revise",
                    "last_gate_outcome": "retry",
                    "status": "active",
                },
                "visual": {
                    "procedure": [
                        {"key": "review", "label": "Review", "status": "completed"},
                        {"key": "revise", "label": "Revise", "status": "active"},
                        {"key": "gate", "label": "Gate", "status": "pending"},
                    ],
                    "gates": [
                        {"key": "retry", "label": "Retry Gate", "status": "completed", "target_phase": "review"},
                        {"key": "accept", "label": "Accept Gate", "status": "pending", "target_phase": "decision"},
                    ],
                },
            }
        if path == "/api/agent-runtime-status":
            return {"agents": {}}
        if path == "/api/runtime/logs":
            return {"events": []}
        if path == "/api/system-mailbox":
            return {"count": 0, "reports": []}
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    rendered = main._render_hud("thread-1", limit=5)

    assert "Protocol Progress" in rendered
    assert "Active cycle: 2" in rendered
    assert "Completed cycles: 1" in rendered
    assert "Procedure" in rendered
    assert "Review" in rendered
    assert "Revise" in rendered
    assert "Retry Gate" in rendered
    assert "Accept Gate" in rendered
    assert "Current:" not in rendered
    assert "Last gate:" not in rendered


def test_protocol_progress_active_node_animates_between_frames(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    phase_cycle_payload = {
        "state": {
            "thread_id": "thread-1",
            "phase_name": "review",
            "cycle_index": 2,
            "procedure_steps": ["review", "revise"],
            "current_step_label": "revise",
            "last_gate_outcome": "retry",
            "status": "active",
        },
        "visual": {
            "procedure": [
                {"key": "review", "label": "Review", "status": "completed"},
                {"key": "revise", "label": "Revise", "status": "active"},
                {"key": "gate", "label": "Gate", "status": "pending"},
            ],
            "gates": [
                {"key": "retry", "label": "Retry Gate", "status": "completed", "target_phase": "review"},
            ],
        },
    }

    frame_one = main._render_protocol_progress_lines(phase_cycle_payload, animation_phase=0)
    frame_two = main._render_protocol_progress_lines(phase_cycle_payload, animation_phase=1)

    assert frame_one != frame_two
    assert any("Revise" in line for line in frame_one)
    assert any("Revise" in line for line in frame_two)


def test_render_thread_runtime_diagnostics_shows_long_term_app_server_sessions(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/agent-runtime-status":
            return {
                "agents": {
                    "alice": [
                        {
                            "thread_id": "thread-1",
                            "transport_mode": "live_process_transport",
                            "status": "done",
                            "fallback_transport_involved": False,
                        }
                    ]
                }
            }
        if path == "/api/runtime/logs":
            return {"events": []}
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    lines = main._render_thread_runtime_diagnostics("thread-1", _attached_config(data_dir))

    assert lines == [
        "[green]alice  transport=live_process_transport  surface=app-server  kind=long-term[/green]",
        "[green]       primary=live_process_transport  status=done  fallback=no  degraded=no  recoverable=no  recovering=no  recovery_attempts=0[/green]",
    ]


def test_render_thread_runtime_diagnostics_shows_recovery_state(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/agent-runtime-status":
            return {
                "agents": {
                    "alice": [
                        {
                            "thread_id": "thread-1",
                            "transport_mode": "resume_invocation_transport",
                            "primary_transport_mode": "live_process_transport",
                            "status": "received",
                            "fallback_transport_involved": True,
                            "recoverable": True,
                            "degraded": True,
                            "recovery_attempts": 1,
                        }
                    ]
                }
            }
        if path == "/api/runtime/logs":
            return {"events": []}
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    lines = main._render_thread_runtime_diagnostics("thread-1", _attached_config(data_dir))

    assert lines == [
        "[yellow]alice  transport=resume_invocation_transport  surface=exec  kind=short-term[/yellow]",
        "[yellow]       primary=live_process_transport  status=received  fallback=yes  degraded=yes  recoverable=yes  recovering=no  recovery_attempts=1[/yellow]",
    ]


def test_follow_render_loop_uses_live_updates_without_clearing(monkeypatch):
    updates: list[str] = []

    class FakeLive:
        def __init__(self, initial, console=None, auto_refresh=False, screen=False):
            updates.append(initial)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, renderable, refresh=False):
            updates.append(renderable)

    def fail_clear():
        raise AssertionError("console.clear should not be called during follow mode")

    sleep_calls = {"count": 0}

    def fake_sleep(_interval: float):
        sleep_calls["count"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "Live", FakeLive)
    monkeypatch.setattr(main.console, "clear", fail_clear)
    monkeypatch.setattr(main.time, "sleep", fake_sleep)

    main._run_live_view(lambda: "HUD frame", interval=0.2)

    assert updates == ["HUD frame", "HUD frame"]


def test_hud_stream_prints_existing_events_for_bound_thread(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    thread = thread_store.create_thread(
        topic="HUD stream thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).append(
        {
            "timestamp": "2026-04-15T02:12:16+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "phase": "context",
            "event_type": "hook_decision",
            "hook_event_name": "Stop",
            "decision": "allow",
            "summary": "Stop allowed.",
        }
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    def fake_sleep(_interval: float):
        raise KeyboardInterrupt

    monkeypatch.setattr(main.time, "sleep", fake_sleep)

    result = runner.invoke(app, ["hud", "--stream"])

    assert result.exit_code == 0, result.output
    assert "B-TWIN HUD stream" in result.output
    assert thread["thread_id"] in result.output
    assert "Stop allow" in result.output
    assert "Stop allowed." in result.output


def test_render_thread_watch_formats_codex_and_btwin_events_for_humans():
    thread = {
        "thread_id": "thread-1",
        "protocol": "debate",
        "current_phase": "context",
        "topic": "HUD thread",
    }
    status_summary = {
        "agents": [
            {"name": "alice", "status": "contributed"},
        ]
    }
    events = [
        {
            "timestamp": "2026-04-15T04:04:50+00:00",
            "thread_id": "thread-1",
            "event_type": "phase_exit_check_requested",
            "source": "codex.hook",
            "agent": "alice",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "hook_event_name": "Stop",
            "summary": "Stop exit check requested.",
        },
        {
            "timestamp": "2026-04-15T04:04:50+00:00",
            "thread_id": "thread-1",
            "event_type": "phase_exit_blocked",
            "source": "btwin.workflow.hook",
            "agent": "alice",
            "phase": "context",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "hook_event_name": "Stop",
            "reason": "missing_contribution",
            "summary": "Current phase context still needs a contribution from alice before stopping.",
        },
    ]

    trace = main._build_thread_watch_trace_rows(thread, events)
    rendered = main._render_thread_watch(thread, status_summary, trace)

    assert "[cyan]04:04:50  CODEX -> BTWIN  Exit check requested[/cyan]" in rendered
    assert "[red]04:04:50  BTWIN -> CODEX  Exit blocked[/red]" in rendered
    assert "agent: alice" in rendered
    assert "phase: context" in rendered
    assert "reason: missing_contribution" in rendered
    assert "session: session-1" in rendered
    assert "turn: turn-1" in rendered
    assert "summary: Stop exit check requested." in rendered


def test_render_thread_watch_colors_allow_and_noop_headlines():
    thread = {
        "thread_id": "thread-1",
        "protocol": "debate",
        "current_phase": "context",
    }
    status_summary = {"agents": [{"name": "alice", "status": "contributed"}]}
    events = [
        {
            "timestamp": "2026-04-15T04:04:46+00:00",
            "thread_id": "thread-1",
            "event_type": "phase_attempt_started",
            "source": "codex.hook",
            "agent": "alice",
            "hook_event_name": "UserPromptSubmit",
            "summary": "Current phase: context. Required result type: contribution.",
        },
        {
            "timestamp": "2026-04-15T04:07:27+00:00",
            "thread_id": "thread-1",
            "event_type": "required_result_recorded",
            "source": "btwin.contribution.submit",
            "agent": "alice",
            "phase": "context",
            "summary": "Stop allowed.",
        },
    ]

    trace = main._build_thread_watch_trace_rows(thread, events)
    rendered = main._render_thread_watch(thread, status_summary, trace)

    assert "[cyan]04:04:46  CODEX -> BTWIN  Phase attempt started[/cyan]" in rendered
    assert "[green]04:07:27  BTWIN -> CODEX  Required result recorded[/green]" in rendered


def test_render_thread_watch_uses_normalized_rows_as_canonical_boundary():
    thread = {
        "thread_id": "thread-1",
        "protocol": "review-loop",
        "current_phase": "review",
    }
    status_summary = {"agents": [{"name": "alice", "status": "contributed"}]}
    trace = [
        {
            "kind": "gate",
            "timestamp": "2026-04-15T04:07:27+00:00",
            "thread_id": "thread-1",
            "phase": "review",
            "cycle_index": 1,
            "next_cycle_index": 2,
            "outcome": "retry",
            "procedure_key": "review-pass",
            "procedure_alias": "Review",
            "gate_key": "retry-loop",
            "gate_alias": "Retry Gate",
            "target_phase": "review",
            "reason": None,
            "summary": "Retry gate advanced review to cycle 2.",
            "source": "btwin.protocol.apply_next",
            "agent": "alice",
            "session_id": None,
            "turn_id": None,
            "event_type": "unexpected_raw_event_name",
            "hook_event_name": None,
            "decision": None,
        }
    ]

    rendered = main._render_thread_watch(thread, status_summary, trace)

    assert "[green]04:07:27  BTWIN -> CODEX  Retry Gate completed[/green]" in rendered
    assert "procedure: Review [review-pass]" in rendered
    assert "gate: Retry Gate [retry-loop]" in rendered
    assert "outcome: retry" in rendered
    assert "target: review" in rendered


def test_build_thread_watch_trace_rows_normalizes_required_fields():
    thread = {
        "thread_id": "thread-1",
        "protocol": "debate",
        "current_phase": "context",
    }
    events = [
        {
            "timestamp": "2026-04-15T04:04:46+00:00",
            "thread_id": "thread-1",
            "event_type": "phase_attempt_started",
            "source": "codex.hook",
            "agent": "alice",
            "phase": "context",
            "summary": "Current phase: context. Required result type: contribution.",
        },
        {
            "timestamp": "2026-04-15T04:07:27+00:00",
            "thread_id": "thread-1",
            "event_type": "runtime_binding_closed",
            "source": "btwin.runtime.binding.cleanup",
            "reason": "stale_last_seen",
            "summary": "Runtime binding closed: stale last seen.",
        },
    ]

    trace = main._build_thread_watch_trace_rows(thread, events)

    assert len(trace) == 2
    assert trace[0]["kind"] == "attempt"
    assert trace[0]["timestamp"] == "2026-04-15T04:04:46+00:00"
    assert trace[0]["thread_id"] == "thread-1"
    assert trace[0]["phase"] == "context"
    assert trace[0]["cycle_index"] is None
    assert trace[0]["next_cycle_index"] is None
    assert trace[0]["outcome"] is None
    assert trace[0]["procedure_key"] is None
    assert trace[0]["procedure_alias"] is None
    assert trace[0]["gate_key"] is None
    assert trace[0]["gate_alias"] is None
    assert trace[0]["target_phase"] is None
    assert trace[0]["reason"] is None
    assert trace[0]["summary"] == "Current phase: context. Required result type: contribution."
    assert trace[0]["source"] == "codex.hook"

    assert trace[1]["kind"] == "runtime"
    assert trace[1]["timestamp"] == "2026-04-15T04:07:27+00:00"
    assert trace[1]["thread_id"] == "thread-1"
    assert trace[1]["phase"] is None
    assert trace[1]["cycle_index"] is None
    assert trace[1]["next_cycle_index"] is None
    assert trace[1]["outcome"] is None
    assert trace[1]["procedure_key"] is None
    assert trace[1]["procedure_alias"] is None
    assert trace[1]["gate_key"] is None
    assert trace[1]["gate_alias"] is None
    assert trace[1]["target_phase"] is None
    assert trace[1]["reason"] == "stale_last_seen"
    assert trace[1]["summary"] == "Runtime binding closed: stale last seen."
    assert trace[1]["source"] == "btwin.runtime.binding.cleanup"


def test_build_thread_watch_trace_rows_uses_canonical_kind_taxonomy():
    thread = {
        "thread_id": "thread-1",
        "protocol": "debate",
        "current_phase": "context",
    }
    events = [
        {"timestamp": "2026-04-15T04:04:45+00:00", "thread_id": "thread-1", "event_type": "hook_decision"},
        {"timestamp": "2026-04-15T04:04:46+00:00", "thread_id": "thread-1", "event_type": "phase_attempt_started"},
        {"timestamp": "2026-04-15T04:04:47+00:00", "thread_id": "thread-1", "event_type": "required_result_recorded"},
        {"timestamp": "2026-04-15T04:04:48+00:00", "thread_id": "thread-1", "event_type": "cycle_gate_completed"},
        {"timestamp": "2026-04-15T04:04:49+00:00", "thread_id": "thread-1", "event_type": "phase_transitioned"},
        {"timestamp": "2026-04-15T04:04:50+00:00", "thread_id": "thread-1", "event_type": "runtime_binding_closed"},
    ]

    trace = main._build_thread_watch_trace_rows(thread, events)

    assert [row["kind"] for row in trace] == ["guard", "attempt", "result", "gate", "phase", "runtime"]


def test_render_thread_watch_adds_app_server_hint_to_agents_summary(monkeypatch, tmp_path):
    data_dir = tmp_path / ".btwin"
    thread = {
        "thread_id": "thread-1",
        "protocol": "debate",
        "current_phase": "context",
    }
    status_summary = {"agents": [{"name": "alice", "status": "contributed"}]}

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(
        main,
        "_runtime_sessions_for_thread",
        lambda thread_id, config: [
            (
                "alice",
                {
                    "thread_id": thread_id,
                    "transport_mode": "live_process_transport",
                    "fallback_transport_involved": False,
                    "recoverable": False,
                },
            )
        ],
    )
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, config: [])

    rendered = main._render_thread_watch(thread, status_summary, [])

    assert "Agents  alice=contributed (app-server)" in rendered


def test_render_thread_watch_adds_exec_fallback_recovery_hint_to_agents_summary(monkeypatch, tmp_path):
    data_dir = tmp_path / ".btwin"
    thread = {
        "thread_id": "thread-1",
        "protocol": "debate",
        "current_phase": "context",
    }
    status_summary = {"agents": [{"name": "alice", "status": "contributed"}]}

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(
        main,
        "_runtime_sessions_for_thread",
        lambda thread_id, config: [
            (
                "alice",
                {
                    "thread_id": thread_id,
                    "transport_mode": "resume_invocation_transport",
                    "fallback_transport_involved": True,
                    "recoverable": True,
                },
            )
        ],
    )
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, config: [])

    rendered = main._render_thread_watch(thread, status_summary, [])

    assert "Agents  alice=contributed (exec fallback, recoverable)" in rendered


def test_render_thread_watch_adds_recovering_hint_to_agents_summary(monkeypatch, tmp_path):
    data_dir = tmp_path / ".btwin"
    thread = {
        "thread_id": "thread-1",
        "protocol": "debate",
        "current_phase": "context",
    }
    status_summary = {"agents": [{"name": "alice", "status": "contributed"}]}

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(
        main,
        "_runtime_sessions_for_thread",
        lambda thread_id, config: [
            (
                "alice",
                {
                    "thread_id": thread_id,
                    "transport_mode": "resume_invocation_transport",
                    "fallback_transport_involved": True,
                    "recoverable": False,
                    "recovery_pending": True,
                },
            )
        ],
    )
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, config: [])

    rendered = main._render_thread_watch(thread, status_summary, [])

    assert "Agents  alice=contributed (exec fallback, recovering)" in rendered


def test_hud_attached_mode_shows_thread_lookup_error_instead_of_exiting(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    RuntimeBindingStore(project_root / ".btwin").bind("thread-missing", "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_service_data_dir", lambda: project_root / ".btwin")

    def fake_api_get(path: str, params: dict | None = None):
        raise RuntimeError(f"404 thread missing for {path}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "binding=alice" in result.output
    assert "thread lookup error" in result.output
    assert "thread-missing" in result.output


def test_hud_key_parser_understands_arrow_and_control_keys():
    assert main._hud_key_from_bytes(b"\x1b[A") == "up"
    assert main._hud_key_from_bytes(b"\x1b[B") == "down"
    assert main._hud_key_from_bytes(b"\x1b[5~") == "page_up"
    assert main._hud_key_from_bytes(b"\x1b[6~") == "page_down"
    assert main._hud_key_from_bytes(b"\x1b[H") == "home"
    assert main._hud_key_from_bytes(b"\x1b[F") == "end"
    assert main._hud_key_from_bytes(b"\r") == "enter"
    assert main._hud_key_from_bytes(b"b") == "back"
    assert main._hud_key_from_bytes(b"c") == "close"
    assert main._hud_key_from_bytes(b"j") == "down"
    assert main._hud_key_from_bytes(b"k") == "up"
    assert main._hud_key_from_bytes(b"f") == "end"
    assert main._hud_key_from_bytes(b"q") == "quit"
    assert main._hud_key_from_bytes(b"t") == "threads"
    assert main._hud_key_from_bytes(b"d") == "detail"
    assert main._hud_key_from_bytes(b"v") == "validation"
    assert main._hud_key_from_bytes(b"l") == "live"


def test_hud_menu_escapes_literal_brackets_for_rich():
    rendered = main._render_hud_menu(main._HudNavigatorState())
    buffer = io.StringIO()
    Console(file=buffer, force_terminal=False, color_system=None).print(rendered)

    assert "> [threads]" in buffer.getvalue()


def test_hud_navigator_moves_from_menu_to_threads_to_thread_view(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads":
            return [
                {
                    "thread_id": "thread-1",
                    "topic": "First thread",
                    "protocol": "debate",
                    "current_phase": "context",
                    "status": "active",
                },
                {
                    "thread_id": "thread-2",
                    "topic": "Second thread",
                    "protocol": "debate",
                    "current_phase": "discussion",
                    "status": "active",
                },
            ]
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    assert main._apply_hud_key(state, "enter", config) is False
    assert state.screen == "threads"

    assert main._apply_hud_key(state, "down", config) is False
    assert state.thread_index == 1

    assert main._apply_hud_key(state, "enter", config) is False
    assert state.screen == "thread"
    assert state.selected_thread_id == "thread-2"

    assert main._apply_hud_key(state, "back", config) is False
    assert state.screen == "threads"


def test_hud_thread_view_scrolls_logs(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(
        main,
        "_render_hud_thread_detail_screen",
        lambda thread_id, limit: "\n".join(
            [
                "B-TWIN HUD :: Thread Detail :: mode=attached",
                "",
                *[f"line {i}" for i in range(12)],
                "",
                "Hint      up/down scroll  pgup/pgdn page  home/end jump",
                "Nav       [T]hreads  [D]etail  [V]alidation  [L]ive  [:] cmd  [q] quit",
            ]
        ),
    )
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 4)

    assert main._apply_hud_key(state, "down", config) is False
    assert state.thread_log_offset == 1

    assert main._apply_hud_key(state, "page_down", config) is False
    assert state.thread_log_offset == 5

    assert main._apply_hud_key(state, "end", config) is False
    assert state.thread_log_offset == 8

    assert main._apply_hud_key(state, "home", config) is False
    assert state.thread_log_offset == 0


def test_hud_navigator_can_jump_between_threads_detail_and_live(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads":
            return [
                {
                    "thread_id": "thread-1",
                    "topic": "First thread",
                    "protocol": "debate",
                    "current_phase": "context",
                    "status": "active",
                }
            ]
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    assert main._apply_hud_key(state, "enter", config) is False
    assert state.screen == "threads"

    assert main._apply_hud_key(state, "detail", config) is False
    assert state.screen == "thread"
    assert state.selected_thread_id == "thread-1"

    assert main._apply_hud_key(state, "live", config) is False
    assert state.screen == "live"

    assert main._apply_hud_key(state, "detail", config) is False
    assert state.screen == "thread"

    assert main._apply_hud_key(state, "threads", config) is False
    assert state.screen == "threads"


def test_hud_threads_view_uses_wireframe_list_and_selected_preview(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="threads", thread_index=0)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(
        main,
        "_list_hud_threads",
        lambda current_config: [
            {
                "thread_id": "thread-1",
                "topic": "Design Review",
                "protocol": "review-loop",
                "current_phase": "review",
            },
            {
                "thread_id": "thread-2",
                "topic": "Onboarding",
                "protocol": "onboarding",
                "current_phase": "intro",
            },
        ],
    )
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Design Review",
                "protocol": "review-loop",
                "current_phase": "review",
            },
            {"agents": [{"name": "jun", "status": "waiting"}, {"name": "ari", "status": "joined"}]},
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_get_protocol_store",
        lambda: type(
            "FakeProtocolStore",
            (),
            {
                "get_protocol": lambda self, name: Protocol(
                    name="review-loop",
                    phases=[
                        ProtocolPhase(name="context"),
                        ProtocolPhase(
                            name="review",
                            procedure=[
                                {"role": "facilitator", "action": "announce", "alias": "Announce"},
                                {
                                    "role": "reviewer",
                                    "action": "collect-feedback",
                                    "alias": "Collect Feedback",
                                    "key": "collect-feedback",
                                },
                                {"role": "facilitator", "action": "resolve", "alias": "Resolve"},
                            ],
                        ),
                        ProtocolPhase(name="decision"),
                    ],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "trace": [
                {
                    "timestamp": "2026-04-19T12:03:55Z",
                    "kind": "gate",
                    "phase": "review",
                    "gate_alias": "Retry Gate",
                    "summary": "Retry loop completed.",
                }
            ],
            "phase_cycle": {
                "state": {"cycle_index": 3, "current_step_label": "collect-feedback"},
            },
        },
    )
    monkeypatch.setattr(
        main,
        "_runtime_sessions_for_thread",
        lambda thread_id, current_config: [
            ("jun", {"transport_mode": "live_process_transport", "status": "waiting"}),
            ("ari", {"transport_mode": "live_process_transport", "status": "done"}),
        ],
    )

    rendered = main._render_hud_threads(state, config, limit=5)

    assert "Threads / Sessions" in rendered
    assert "Filter: all" in rendered
    assert "> Design Review" in rendered
    assert "Onboarding" in rendered
    assert "Selected Workflow" in rendered
    assert "phase: review (cycle 3)" in rendered
    assert "gate: Retry Gate" in rendered
    assert "agents: jun=waiting(app-server)  ari=joined(app-server)" in rendered
    assert "last: Retry loop completed." in rendered


def test_hud_threads_view_uses_shared_tui_chrome(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="threads", thread_index=0)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(
        main,
        "_list_hud_threads",
        lambda current_config: [{"thread_id": "thread-1", "topic": "Design Review", "protocol": "review-loop", "current_phase": "review"}],
    )
    monkeypatch.setattr(main, "_try_load_thread_snapshot", lambda thread_id, current_config: (None, None, "missing"))

    rendered = main._render_hud_threads(state, config, limit=5)

    assert rendered.splitlines()[0] == "B-TWIN HUD :: Threads / Sessions :: mode=attached"
    assert "Hint      up/down select  enter open  d detail  l live  c close" in rendered
    assert "Nav       [T]hreads  [D]etail  [V]alidation  [L]ive  [:] cmd  [q] quit" in rendered


def test_hud_threads_renderable_uses_real_panels(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="threads", thread_index=0)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(
        main,
        "_list_hud_threads",
        lambda current_config: [{"thread_id": "thread-1", "topic": "Design Review", "protocol": "review-loop", "current_phase": "review"}],
    )
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {"thread_id": thread_id, "topic": "Design Review", "protocol": "review-loop", "current_phase": "review"},
            {"agents": [{"name": "jun", "status": "waiting"}]},
            None,
        ),
    )
    monkeypatch.setattr(main, "_workflow_event_log", lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})())
    monkeypatch.setattr(main, "_thread_watch_payload", lambda thread, status, events: {"trace": [], "phase_cycle": None})
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, current_config: [])

    renderable = main._render_hud_navigator_renderable(state, config, limit=5)

    assert not isinstance(renderable, str)
    rendered = _renderable_to_text(renderable)
    assert "Threads / Sessions" in rendered
    assert "Selected Workflow" in rendered


def test_hud_thread_detail_renderable_shows_validation_panel(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {"thread_id": thread_id, "topic": "Design Review", "protocol": "review-loop", "current_phase": "review"},
            {"agents": [{"name": "jun", "status": "waiting"}]},
            None,
        ),
    )
    monkeypatch.setattr(main, "_workflow_event_log", lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})())
    monkeypatch.setattr(
        main,
        "_get_protocol_store",
        lambda: type(
            "FakeProtocolStore",
            (),
            {
                "get_protocol": lambda self, name: Protocol(
                    name="review-loop",
                    phases=[
                        ProtocolPhase(name="context"),
                        ProtocolPhase(
                            name="review",
                            procedure=[
                                {"role": "facilitator", "action": "announce", "alias": "Announce"},
                                {
                                    "role": "reviewer",
                                    "action": "collect-feedback",
                                    "alias": "Collect Feedback",
                                    "key": "collect-feedback",
                                },
                                {"role": "facilitator", "action": "resolve", "alias": "Resolve"},
                            ],
                        ),
                        ProtocolPhase(name="decision"),
                    ],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "phase_cycle": {
                "state": {"cycle_index": 3, "current_step_label": "collect-feedback"},
                "context_core": {"outcome_policy": "review-outcomes", "policy_outcomes": ["retry", "accept", "close"]},
                "visual": {"procedure": [{"key": "collect-feedback", "label": "Collect Feedback", "status": "active"}], "gates": [{"key": "retry", "label": "Retry Gate", "status": "active"}]},
            },
            "trace": [
                {"timestamp": "2026-04-19T12:03:55Z", "kind": "guard", "decision": "block", "phase": "review", "reason": "missing_contribution", "summary": "Missing contribution for current phase."}
            ],
        },
    )
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, current_config: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    renderable = main._render_hud_navigator_renderable(state, config, limit=5)

    rendered = _renderable_to_text(renderable)
    assert "Thread Detail" in rendered
    assert "review-loop" in rendered
    assert "Context" in rendered and "Review" in rendered and "Decision" in rendered
    assert "Announce" in rendered and "Collect Feedback" in rendered and "Resolve" in rendered
    assert "Protocol / Phase" not in rendered
    assert "Gate / Guard Focus" not in rendered
    assert "Recent Activity" in rendered
    assert "Validation" not in rendered


def test_hud_validation_renderable_shows_validation_sections(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="validation", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {"thread_id": thread_id, "topic": "Design Review", "protocol": "review-loop", "current_phase": "review"},
            {"agents": [{"name": "jun", "status": "waiting"}]},
            None,
        ),
    )
    monkeypatch.setattr(main, "_workflow_event_log", lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})())
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "phase_cycle": {
                "state": {"cycle_index": 3, "current_step_label": "collect-feedback"},
                "context_core": {"outcome_policy": "review-outcomes", "policy_outcomes": ["retry", "accept", "close"]},
                "visual": {"procedure": [{"key": "collect-feedback", "label": "Collect Feedback", "status": "active"}], "gates": [{"key": "retry", "label": "Retry Gate", "status": "active"}]},
            },
            "trace": [
                {"timestamp": "2026-04-19T12:03:55Z", "kind": "guard", "decision": "block", "phase": "review", "reason": "missing_contribution", "summary": "Missing contribution for current phase."}
            ],
        },
    )
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, current_config: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    renderable = main._render_hud_navigator_renderable(state, config, limit=5)

    rendered = _renderable_to_text(renderable, width=160)
    assert "Validation Focus" in rendered
    assert "Rule Compliance" in rendered
    assert "Protocol match" in rendered
    assert "Required contribution" in rendered
    assert "verdict" in rendered
    assert "Expected" in rendered
    assert "Actual" in rendered
    assert "Missing contribution blocked" in rendered


def test_hud_validation_focus_uses_protocol_next_for_validation_gap(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {"thread_id": thread_id, "topic": "Design Review", "protocol": "code-review", "current_phase": "analysis"},
            {"agents": [{"name": "jun", "status": "joined"}]},
            None,
        ),
    )
    monkeypatch.setattr(main, "_workflow_event_log", lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})())
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "phase_cycle": {"state": {"cycle_index": 1, "current_step_label": "gate"}},
            "trace": [
                {
                    "timestamp": "2026-04-19T12:04:28Z",
                    "kind": "gate",
                    "phase": "analysis",
                    "reason": "missing_contribution",
                    "summary": "Missing contribution for current phase.",
                }
            ],
        },
    )
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, current_config: [])
    monkeypatch.setattr(
        main,
        "_try_protocol_next_snapshot",
        lambda thread_id, current_config: {
            "passed": False,
            "missing": [{"agent": "jun", "missing_sections": ["scope", "findings"]}],
            "suggested_action": "submit_contribution",
        },
        raising=False,
    )

    rendered = main._render_hud_validation_focus_screen("thread-1", limit=5)

    assert "Validation verdict  WARN" in rendered
    assert "Primary reason  jun missing scope, findings" in rendered
    assert "Rule Compliance" in rendered
    assert "Required contribution: WARN" in rendered
    assert "primary_reason: jun missing scope, findings" in rendered
    assert "next expected action: submit_contribution" in rendered
    assert "Next action  submit contribution" in rendered
    assert "Reasons" in rendered
    assert "- jun missing scope, findings" in rendered
    assert "Why this verdict" not in rendered
    assert "Validation Cases" not in rendered
    assert "Trace / Reason Excerpt" not in rendered


def test_validation_compliance_rows_prioritize_active_cases_above_skips():
    validation = {
        "checks": [("protocol_match", "PASS")],
        "reasons": [],
        "verdict": "PASS",
        "next_expected_action": "none",
    }
    validation_cases = [
        "happy_path_accept: not triggered",
        "retry_same_phase: PASS",
        "missing_contribution_blocked: not triggered",
        "close_requires_summary: ready",
    ]

    rows = main._validation_compliance_rows(validation, validation_cases, runtime_sessions={}, trace_rows=[])

    case_rows = [row for row in rows if row["group"] == "case"]

    assert [row["key"] for row in case_rows] == [
        "retry_same_phase",
        "happy_path_accept",
        "missing_contribution_blocked",
        "close_requires_summary",
    ]


def test_run_hud_navigator_uses_renderable_builder(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    updates: list[object] = []

    class FakeLive:
        def __init__(self, initial, console=None, auto_refresh=False, screen=False):
            updates.append(initial)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, renderable, refresh=False):
            updates.append(renderable)

    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(main, "Live", FakeLive)
    monkeypatch.setattr(main, "_HudRawInput", lambda: type("C", (), {"__enter__": lambda self: self, "__exit__": lambda self, exc_type, exc, tb: False})())
    monkeypatch.setattr(main, "_read_hud_key", lambda interval: "quit")
    monkeypatch.setattr(
        main,
        "_render_hud_navigator_renderable",
        lambda state, current_config, limit, animation_phase=None, snapshot=None: {"kind": "rich-hud"},
    )

    main._run_hud_navigator(limit=5, interval=0.01)

    assert updates == [{"kind": "rich-hud"}, {"kind": "rich-hud"}]


def test_run_hud_navigator_reuses_snapshot_between_animation_frames(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    render_snapshots: list[object] = []
    snapshot_calls: list[str] = []

    class FakeLive:
        def __init__(self, initial, console=None, auto_refresh=False, screen=False):
            render_snapshots.append(initial.get("snapshot"))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, renderable, refresh=False):
            render_snapshots.append(renderable.get("snapshot"))

    keys = iter([None, "quit"])
    monotonic_values = iter([0.0, 0.05, 0.10])

    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(main, "Live", FakeLive)
    monkeypatch.setattr(main, "_HudRawInput", lambda: type("C", (), {"__enter__": lambda self: self, "__exit__": lambda self, exc_type, exc, tb: False})())
    monkeypatch.setattr(main, "_read_hud_key", lambda interval: next(keys))
    monkeypatch.setattr(main.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        main,
        "_snapshot_hud_navigator_screen",
        lambda state, current_config, limit: snapshot_calls.append(state.screen) or {"id": len(snapshot_calls)},
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "_render_hud_navigator_renderable",
        lambda state, current_config, limit, animation_phase=None, snapshot=None: {"kind": "rich-hud", "snapshot": snapshot},
    )

    main._run_hud_navigator(limit=5, interval=1.0)

    assert snapshot_calls == ["menu"]
    assert render_snapshots == [{"id": 1}, {"id": 1}, {"id": 1}]


def test_apply_hud_key_opens_validation_from_thread(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)

    handled = main._apply_hud_key(state, "validation", config)

    assert handled is False
    assert state.screen == "validation"
    assert state.thread_log_offset == 0


def test_hud_live_trace_view_renders_diagnostics_title(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="live", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Trace thread",
                "protocol": "debate",
                "current_phase": "review",
            },
            {"agents": []},
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(main, "_thread_watch_payload", lambda thread, status, events: {"trace": []})
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, current_config: [])
    monkeypatch.setattr(main, "_list_system_mailbox_reports", lambda **kwargs: [])

    rendered = main._render_hud_navigator(state, config, limit=5)

    assert "Live Trace / Diagnostics" in rendered
    assert "Stream    LIVE  rows=0  filter=all" in rendered
    assert "Row Inspector" in rendered
    assert "No trace rows" in rendered


def test_hud_live_trace_view_surfaces_guard_and_gate_rows(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="live", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Trace thread",
                "protocol": "debate",
                "current_phase": "review",
            },
            {"agents": []},
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_get_protocol_store",
        lambda: type(
            "FakeProtocolStore",
            (),
            {
                "get_protocol": lambda self, name: Protocol(
                    name="review-loop",
                    phases=[
                        ProtocolPhase(name="context"),
                        ProtocolPhase(
                            name="review",
                            procedure=[
                                {"role": "facilitator", "action": "announce", "alias": "Announce"},
                                {
                                    "role": "reviewer",
                                    "action": "collect-feedback",
                                    "alias": "Collect Feedback",
                                    "key": "collect-feedback",
                                },
                                {"role": "facilitator", "action": "resolve", "alias": "Resolve"},
                            ],
                        ),
                        ProtocolPhase(name="decision"),
                    ],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "trace": [
                {
                    "timestamp": "2026-04-19T04:07:26Z",
                    "kind": "guard",
                    "hook_event_name": "Stop",
                    "decision": "block",
                    "phase": "review",
                    "reason": "missing_contribution",
                    "baseline_guard": "contribution_required",
                    "summary": "Missing contribution for current phase.",
                },
                {
                    "timestamp": "2026-04-19T04:07:27Z",
                    "kind": "gate",
                    "phase": "review",
                    "gate_alias": "Retry Gate",
                    "gate_key": "retry-loop",
                    "target_phase": "review",
                    "outcome": "retry",
                    "summary": "Retry loop completed.",
                },
            ]
        },
    )
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, current_config: [])
    monkeypatch.setattr(main, "_list_system_mailbox_reports", lambda **kwargs: [])

    rendered = main._render_hud_navigator(state, config, limit=5)

    assert "04:07:27  gate" in rendered
    assert "04:07:26  guard" in rendered
    assert "Retry loop completed." in rendered
    assert "Missing contribution for current phase." in rendered
    assert "Row Inspector" in rendered
    assert "kind: gate" in rendered
    assert '"gate_key": "retry-loop"' in rendered


def test_hud_live_trace_view_uses_wireframe_sections_and_inspector(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="live", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Design Review",
                "protocol": "review-loop",
                "current_phase": "review",
            },
            {"agents": [{"name": "jun", "status": "waiting"}, {"name": "ari", "status": "joined"}]},
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "trace": [
                {
                    "timestamp": "2026-04-19T12:03:55Z",
                    "kind": "gate",
                    "phase": "review",
                    "gate_alias": "Retry Gate",
                    "gate_key": "retry-loop",
                    "target_phase": "review",
                    "outcome": "retry",
                    "summary": "Retry loop completed.",
                    "outcome_policy": "review-outcomes",
                    "outcome_emitters": ["reviewer", "author"],
                    "policy_outcomes": ["retry", "accept", "close"],
                },
                {
                    "timestamp": "2026-04-19T12:04:28Z",
                    "kind": "result",
                    "phase": "review",
                    "agent": "jun",
                    "summary": "LGTM with small nits",
                },
            ]
        },
    )
    monkeypatch.setattr(
        main,
        "_runtime_sessions_for_thread",
        lambda thread_id, current_config: [
            ("jun", {"transport_mode": "live_process_transport", "status": "waiting"}),
            ("ari", {"transport_mode": "live_process_transport", "status": "done"}),
        ],
    )
    monkeypatch.setattr(main, "_list_system_mailbox_reports", lambda **kwargs: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    rendered = main._render_hud_navigator(state, config, limit=5)

    assert "Live Trace / Diagnostics" in rendered
    assert "Stream    LIVE  rows=2  filter=all" in rendered
    assert "Focus     review-loop  phase=review" in rendered
    assert "TIME      KIND" in rendered
    assert "12:04:28  result" in rendered
    assert "12:03:55  gate" in rendered
    assert "Row Inspector" in rendered
    assert "kind: result" in rendered
    assert 'raw: {"agent": "jun", "kind": "result"' in rendered
    assert "Sessions  jun=waiting(app-server)  ari=joined(app-server)" in rendered


def test_hud_thread_detail_view_uses_shared_tui_chrome(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: ({"thread_id": thread_id}, {}, None),
    )
    monkeypatch.setattr(
        main,
        "_render_hud_thread_detail_screen",
        lambda thread_id, limit: "\n".join(
            [
                "B-TWIN HUD :: Thread Detail :: mode=attached",
                "",
                "Topic     Design Review",
                "",
                "Hint      up/down scroll  pgup/pgdn page  home/end jump",
                "Nav       [T]hreads  [D]etail  [V]alidation  [L]ive  [:] cmd  [q] quit",
            ]
        ),
    )
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 20)

    rendered = main._render_hud_navigator(state, config, limit=5)

    assert rendered.splitlines()[0] == "B-TWIN HUD :: Thread Detail :: mode=attached"
    assert "Hint      up/down scroll  pgup/pgdn page  home/end jump" in rendered
    assert "Nav       [T]hreads  [D]etail  [V]alidation  [L]ive  [:] cmd  [q] quit" in rendered


def test_hud_thread_detail_renders_status_policy_activity_and_hints(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Design Review",
                "protocol": "review-loop",
                "current_phase": "review",
            },
            {
                "agents": [
                    {"name": "jun", "status": "waiting"},
                    {"name": "ari", "status": "joined"},
                ]
            },
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_get_protocol_store",
        lambda: type(
            "FakeProtocolStore",
            (),
            {
                "get_protocol": lambda self, name: Protocol(
                    name="review-loop",
                    phases=[
                        ProtocolPhase(name="context"),
                        ProtocolPhase(
                            name="review",
                            procedure=[
                                {"role": "facilitator", "action": "announce", "alias": "Announce"},
                                {
                                    "role": "reviewer",
                                    "action": "collect-feedback",
                                    "alias": "Collect Feedback",
                                    "key": "collect-feedback",
                                },
                                {"role": "facilitator", "action": "resolve", "alias": "Resolve"},
                            ],
                        ),
                        ProtocolPhase(name="decision"),
                    ],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "phase_cycle": {
                "state": {
                    "cycle_index": 3,
                    "current_step_label": "collect-feedback",
                    "current_step_index": 1,
                    "status": "active",
                },
                "context_core": {
                    "outcome_policy": "review-outcomes",
                    "outcome_emitters": ["reviewer", "author"],
                    "outcome_actions": ["advance", "stay", "end"],
                    "policy_outcomes": ["retry", "accept", "close"],
                },
                "visual": {
                    "procedure": [
                        {"key": "announce", "label": "Announce", "status": "completed"},
                        {"key": "collect-feedback", "label": "Collect Feedback", "status": "active"},
                        {"key": "resolve", "label": "Resolve", "status": "pending"},
                    ],
                    "gates": [
                        {"key": "retry", "label": "Retry Gate", "status": "completed", "target_phase": "review"},
                        {"key": "accept", "label": "Accept Gate", "status": "pending", "target_phase": "decision"},
                    ],
                },
            },
            "trace": [
                {
                    "timestamp": "2026-04-19T12:03:55Z",
                    "kind": "guard",
                    "hook_event_name": "Stop",
                    "decision": "block",
                    "phase": "review",
                    "reason": "missing_contribution",
                    "baseline_guard": "contribution_required",
                    "summary": "Missing contribution for current phase.",
                    "procedure_alias": "Collect Feedback",
                    "procedure_key": "collect-feedback",
                    "gate_alias": "Retry Gate",
                    "gate_key": "retry-loop",
                    "target_phase": "review",
                    "outcome_policy": "review-outcomes",
                    "outcome_emitters": ["reviewer", "author"],
                    "outcome_actions": ["advance", "stay", "end"],
                    "policy_outcomes": ["retry", "accept", "close"],
                },
                {
                    "timestamp": "2026-04-19T12:04:28Z",
                    "kind": "result",
                    "phase": "review",
                    "agent": "jun",
                    "summary": "LGTM with small nits",
                },
            ],
        },
    )
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, config: [])
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, config: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    rendered = main._render_hud_navigator(state, config, limit=5)

    assert "Thread Detail" in rendered
    assert "Design Review" in rendered
    assert "Protocol   review-loop" in rendered
    assert "Phase      Context · • Review · Decision" in rendered
    assert "Procedure  Announce · • Collect Feedback · Resolve" in rendered
    assert "Cycle      3" in rendered
    assert "Status     BLOCKED · gate Retry Gate · guard contribution_required · next submit contribution" in rendered
    assert "Protocol / Phase" not in rendered
    assert "Gate / Guard Focus" not in rendered
    assert "Agent Sessions" in rendered
    assert "jun  waiting" in rendered
    assert "BLOCKED" in rendered
    assert "Collect Feedback" in rendered
    assert "Recent Activity" in rendered
    assert "Quick Actions" not in rendered
    assert "Protocol Notes" not in rendered
    assert "Exit blocked" in rendered
    assert "LGTM with small nits" in rendered


def test_hud_thread_detail_omits_procedure_flow_when_procedure_data_missing(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Design Review",
                "protocol": "review-loop",
                "current_phase": "review",
            },
            {"agents": [{"name": "jun", "status": "waiting"}]},
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_get_protocol_store",
        lambda: type(
            "FakeProtocolStore",
            (),
            {
                "get_protocol": lambda self, name: Protocol(
                    name="review-loop",
                    phases=[
                        ProtocolPhase(name="context"),
                        ProtocolPhase(name="review"),
                        ProtocolPhase(name="decision"),
                    ],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "phase_cycle": {
                "state": {
                    "cycle_index": 3,
                    "current_step_label": "collect-feedback",
                    "status": "active",
                },
                "visual": {},
            },
            "trace": [],
        },
    )
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, config: [])
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, config: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    rendered = main._render_hud_navigator(state, config, limit=5)

    assert "Phase      Context · • Review · Decision" in rendered
    assert "Procedure  " not in rendered
    assert "Procedure  None" not in rendered


def test_hud_thread_detail_marks_first_procedure_step_when_runtime_step_missing(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Procedure Demo",
                "protocol": "procedure-demo",
                "current_phase": "review",
            },
            {"agents": [{"name": "jun", "status": "waiting"}]},
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_get_protocol_store",
        lambda: type(
            "FakeProtocolStore",
            (),
            {
                "get_protocol": lambda self, name: Protocol(
                    name="procedure-demo",
                    phases=[
                        ProtocolPhase(name="context"),
                        ProtocolPhase(
                            name="review",
                            procedure=[
                                {"role": "reviewer", "action": "inspect", "alias": "Inspect", "key": "inspect"},
                                {"role": "reviewer", "action": "revise", "alias": "Revise", "key": "revise"},
                                {"role": "reviewer", "action": "confirm", "alias": "Confirm", "key": "confirm"},
                            ],
                        ),
                        ProtocolPhase(name="decision"),
                    ],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "phase_cycle": {
                "synthetic": True,
                "state": {
                    "cycle_index": 1,
                    "current_step_label": None,
                    "status": "active",
                },
                "visual": {
                    "procedure": [
                        {"key": "gate", "label": "Gate", "status": "pending"},
                    ],
                },
            },
            "trace": [],
        },
    )
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, config: [])
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, config: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    rendered = main._render_hud_navigator(state, config, limit=5)

    assert "Phase      Context · • Review · Decision" in rendered
    assert "Procedure  • Inspect · Revise · Confirm" in rendered


def test_hud_thread_detail_shows_agent_sessions_and_runtime_summary(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Design Review",
                "protocol": "review-loop",
                "current_phase": "review",
            },
            {"agents": [{"name": "jun", "status": "waiting"}]},
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {"phase_cycle": None, "trace": []},
    )
    monkeypatch.setattr(
        main,
        "_runtime_sessions_for_thread",
        lambda thread_id, config: [
            (
                "jun",
                {
                    "transport_mode": "live_process_transport",
                    "status": "done",
                    "fallback_transport_involved": False,
                },
            )
        ],
    )
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, config: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    rendered = main._render_hud_navigator(state, config, limit=5)

    assert "Agent Sessions" in rendered
    assert "jun  waiting     app-server" in rendered


def test_hud_direct_thread_entry_uses_thread_detail_renderer(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_hud_is_interactive", lambda: False)
    monkeypatch.setattr(main, "_render_hud_thread_detail_screen", lambda thread_id, limit: "Thread Detail\nDirect entry")

    result = runner.invoke(app, ["hud", "--thread", "thread-1"])

    assert result.exit_code == 0, result.output
    assert "Thread Detail" in result.output
    assert "Direct entry" in result.output


def test_hud_direct_thread_entry_uses_thread_detail_lookup_error_stub(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_hud_is_interactive", lambda: False)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (None, None, "thread lookup error: missing thread"),
    )
    monkeypatch.setattr(
        main,
        "_render_hud",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback HUD should not be used")),
    )

    result = runner.invoke(app, ["hud", "--thread", "thread-missing"])

    assert result.exit_code == 0, result.output
    assert "Thread Detail" in result.output
    assert "Status   thread lookup error: missing thread" in result.output
    assert "missing thread" in result.output
    assert "fallback HUD" not in result.output


def test_hud_thread_detail_navigator_uses_same_render_path(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(main, "_hud_is_interactive", lambda: False)
    monkeypatch.setattr(main, "_try_load_thread_snapshot", lambda thread_id, current_config: ({}, {}, None))
    monkeypatch.setattr(main, "_workflow_event_log", lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})())
    monkeypatch.setattr(
        main,
        "_render_hud_thread_detail_screen",
        lambda thread_id, limit: "\n".join(
            [
                "B-TWIN HUD :: Thread Detail :: mode=attached",
                "",
                f"shared:{thread_id}:{limit}",
                "",
                "Hint      up/down scroll  pgup/pgdn page  home/end jump",
                "Nav       [T]hreads  [D]etail  [V]alidation  [L]ive  [:] cmd  [q] quit",
            ]
        ),
    )

    rendered = main._render_hud_navigator(state, config, limit=5)

    assert "Thread Detail" in rendered
    assert "shared:thread-1:5" in rendered


def test_hud_thread_detail_renders_cockpit_sections_in_stable_order(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Design Review",
                "protocol": "review-loop",
                "current_phase": "review",
            },
            {
                "agents": [
                    {"name": "jun", "status": "waiting"},
                    {"name": "ari", "status": "joined"},
                ]
            },
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_get_protocol_store",
        lambda: type(
            "FakeProtocolStore",
            (),
            {
                "get_protocol": lambda self, name: Protocol(
                    name="review-loop",
                    phases=[
                        ProtocolPhase(name="context"),
                        ProtocolPhase(
                            name="review",
                            procedure=[
                                {"role": "facilitator", "action": "announce", "alias": "Announce"},
                                {
                                    "role": "reviewer",
                                    "action": "collect-feedback",
                                    "alias": "Collect Feedback",
                                    "key": "collect-feedback",
                                },
                                {"role": "facilitator", "action": "resolve", "alias": "Resolve"},
                            ],
                        ),
                        ProtocolPhase(name="decision"),
                    ],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "phase_cycle": {
                "state": {
                    "cycle_index": 3,
                    "current_step_label": "collect-feedback",
                    "current_step_index": 1,
                    "status": "active",
                },
                "context_core": {
                    "outcome_policy": "review-outcomes",
                    "outcome_emitters": ["reviewer", "author"],
                    "outcome_actions": ["advance", "stay", "end"],
                    "policy_outcomes": ["retry", "accept", "close"],
                },
                "visual": {
                    "procedure": [
                        {"key": "announce", "label": "Announce", "status": "completed"},
                        {"key": "collect-feedback", "label": "Collect Feedback", "status": "active"},
                        {"key": "resolve", "label": "Resolve", "status": "pending"},
                    ],
                    "gates": [
                        {"key": "retry", "label": "Retry Gate", "status": "completed", "target_phase": "review"},
                        {"key": "accept", "label": "Accept Gate", "status": "pending", "target_phase": "decision"},
                    ],
                },
            },
            "trace": [
                {
                    "timestamp": "2026-04-19T12:03:55Z",
                    "kind": "guard",
                    "hook_event_name": "Stop",
                    "decision": "block",
                    "phase": "review",
                    "reason": "missing_contribution",
                    "baseline_guard": "contribution_required",
                    "summary": "Missing contribution for current phase.",
                    "procedure_alias": "Collect Feedback",
                    "procedure_key": "collect-feedback",
                    "gate_alias": "Retry Gate",
                    "gate_key": "retry-loop",
                    "target_phase": "review",
                    "outcome_policy": "review-outcomes",
                    "outcome_emitters": ["reviewer", "author"],
                    "outcome_actions": ["advance", "stay", "end"],
                    "policy_outcomes": ["retry", "accept", "close"],
                },
                {
                    "timestamp": "2026-04-19T12:04:28Z",
                    "kind": "result",
                    "phase": "review",
                    "agent": "jun",
                    "summary": "LGTM with small nits",
                },
            ],
        },
    )
    monkeypatch.setattr(main, "_runtime_sessions_for_thread", lambda thread_id, config: [])
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, config: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    rendered = main._render_hud_navigator(state, config, limit=5)
    lines = rendered.splitlines()

    def index_of(prefix: str) -> int:
        return next(i for i, line in enumerate(lines) if prefix in line)

    assert lines[0] == "B-TWIN HUD :: Thread Detail :: mode=attached"
    assert index_of("Topic") < index_of("Protocol") < index_of("Phase") < index_of("Status")
    assert index_of("Status") < index_of("Recent Activity") < index_of("Agent Sessions")
    assert lines[index_of("Recent Activity") + 1] == "---------------"
    assert lines[index_of("Agent Sessions") + 1] == "--------------"
    assert "Quick Actions" not in rendered
    assert "Protocol / Phase" not in rendered
    assert "Gate / Guard Focus" not in rendered
    assert "thread-1" not in lines[1]
    assert "binding=" not in rendered


def test_hud_validation_focus_warns_on_session_recovery(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="validation", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Design Review",
                "protocol": "review-loop",
                "current_phase": "review",
            },
            {"agents": [{"name": "ari", "status": "joined"}]},
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "phase_cycle": {
                "state": {"cycle_index": 2, "current_step_label": "collect-feedback"},
                "context_core": {"policy_outcomes": ["retry", "accept", "close"]},
            },
            "trace": [
                {
                    "timestamp": "2026-04-19T12:04:28Z",
                    "kind": "result",
                    "phase": "review",
                    "agent": "ari",
                    "summary": "Ari result recorded.",
                }
            ],
        },
    )
    monkeypatch.setattr(
        main,
        "_runtime_sessions_for_thread",
        lambda thread_id, current_config: [
            (
                "ari",
                {
                    "transport_mode": "exec_fallback_transport",
                    "status": "waiting",
                    "fallback_transport_involved": True,
                    "recoverable": True,
                    "recovery_pending": True,
                },
            )
        ],
    )
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, current_config: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    rendered = _renderable_to_text(main._render_hud_navigator_renderable(state, config, limit=5))

    assert "Rule Compliance" in rendered
    assert "WARN" in rendered
    assert "Session health" in rendered
    assert "runtime session recovery pending" in rendered
    assert "Reasons" in rendered


def test_hud_validation_focus_section_contract(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="validation", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_try_load_thread_snapshot",
        lambda thread_id, current_config: (
            {
                "thread_id": thread_id,
                "topic": "Design Review",
                "protocol": "review-loop",
                "current_phase": "review",
            },
            {"agents": [{"name": "jun", "status": "waiting"}]},
            None,
        ),
    )
    monkeypatch.setattr(
        main,
        "_workflow_event_log",
        lambda thread_id: type("FakeLog", (), {"list_events": lambda self, limit: []})(),
    )
    monkeypatch.setattr(
        main,
        "_thread_watch_payload",
        lambda thread, status, events: {
            "phase_cycle": {
                "state": {
                    "phase_name": "review",
                    "cycle_index": 3,
                    "current_step_label": "collect-feedback",
                    "status": "active",
                },
                "context_core": {
                    "outcome_policy": "review-outcomes",
                    "outcome_emitters": ["reviewer", "author"],
                    "outcome_actions": ["advance", "stay", "end"],
                    "policy_outcomes": ["retry", "accept", "close"],
                },
                "visual": {
                    "procedure": [
                        {"key": "announce", "label": "Announce", "status": "completed"},
                        {"key": "collect-feedback", "label": "Collect Feedback", "status": "active"},
                        {"key": "resolve", "label": "Resolve", "status": "pending"},
                    ],
                    "gates": [
                        {"key": "retry", "label": "Retry Gate", "status": "completed", "target_phase": "review"},
                        {"key": "accept", "label": "Accept Gate", "status": "pending", "target_phase": "decision"},
                    ],
                },
            },
            "trace": [
                {
                    "timestamp": "2026-04-19T12:04:28Z",
                    "kind": "result",
                    "phase": "review",
                    "agent": "jun",
                    "summary": "LGTM with small nits",
                }
            ],
        },
    )
    monkeypatch.setattr(
        main,
        "_runtime_sessions_for_thread",
        lambda thread_id, current_config: [
            (
                "jun",
                {
                    "transport_mode": "live_process_transport",
                    "status": "done",
                    "fallback_transport_involved": False,
                },
            )
        ],
    )
    monkeypatch.setattr(main, "_render_thread_runtime_diagnostics", lambda thread_id, current_config: [])
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 200)

    rendered = _renderable_to_text(main._render_hud_navigator_renderable(state, config, limit=5), width=160)
    lines = rendered.splitlines()

    def panel_index(title: str) -> int:
        return next(
            i
            for i, line in enumerate(lines)
            if line.startswith("╭") and title in line
        )

    assert panel_index("Validation") < panel_index("Rule Compliance")
    assert "PASS" in rendered
    assert "all checks aligned" in rendered
    assert "Protocol match" in rendered
    assert "Trajectory match" in rendered
    assert "Session health" in rendered
    assert "Required contribution" in rendered
    assert "Trace completeness" in rendered
    assert "Happy path accept" in rendered
    assert "Retry same phase" in rendered
    assert "Missing contribution blocked" in rendered
    assert "Close requires summary" in rendered
    # PASS verdict => no Reasons panel
    assert "Reasons" not in rendered
    assert "record outcome" in rendered


def test_hud_thread_scroll_bounds_use_detail_renderer_body(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _standalone_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-1")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(
        main,
        "_render_hud",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("scroll bounds should use detail renderer")),
    )
    monkeypatch.setattr(
        main,
        "_render_hud_thread_detail_screen",
        lambda thread_id, limit: "\n".join(
            [
                "B-TWIN HUD :: Thread Detail :: mode=standalone",
                "",
                *[f"line {i}" for i in range(12)],
                "",
                "Hint      up/down scroll  pgup/pgdn page  home/end jump",
                "Nav       [T]hreads  [D]etail  [V]alidation  [L]ive  [:] cmd  [q] quit",
            ]
        ),
    )
    monkeypatch.setattr(main, "_hud_thread_view_window_size", lambda: 4)

    assert main._apply_hud_key(state, "end", config) is False
    assert state.thread_log_offset == 8

    assert main._apply_hud_key(state, "home", config) is False
    assert state.thread_log_offset == 0


def test_hud_close_key_closes_selected_standalone_thread_and_hides_it(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    project_root.mkdir()
    thread = thread_store.create_thread(
        topic="Closable HUD thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    config = _standalone_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id=thread["thread_id"])

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    assert main._apply_hud_key(state, "close", config) is False
    assert state.screen == "threads"
    assert state.selected_thread_id is None

    rendered = main._render_hud_threads(state, config, limit=5)
    assert "No active threads" in rendered

    closed = thread_store.get_thread(thread["thread_id"])
    assert closed is not None
    assert closed["status"] == "completed"


def test_hud_close_key_uses_attached_close_api(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-2")
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_list_hud_threads", lambda config: [{"thread_id": "thread-2"}])

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {"thread_id": "thread-2", "status": "completed"}

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    assert main._apply_hud_key(state, "close", config) is False
    assert state.screen == "threads"
    assert state.selected_thread_id is None
    assert calls == [
        (
            "/api/threads/thread-2/close",
            {"summary": "Closed from B-TWIN HUD."},
        )
    ]


def test_hud_default_uses_navigator_when_interactive(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    called = {"value": False}

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_hud_is_interactive", lambda: True)
    monkeypatch.setattr(main, "_run_hud_navigator", lambda limit, interval: called.__setitem__("value", True))

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert called["value"] is True


def test_hud_threads_picker_allows_selecting_attached_thread(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads":
            return [
                {
                    "thread_id": "thread-1",
                    "topic": "First thread",
                    "protocol": "debate",
                    "current_phase": "context",
                    "status": "active",
                }
            ]
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "First thread",
                "protocol": "debate",
                "current_phase": "context",
            }
        if path == "/api/threads/thread-1/status":
            return {
                "thread_id": "thread-1",
                "current_phase": "context",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    result = runner.invoke(app, ["hud", "--threads"], input="1\n1\n")

    assert result.exit_code == 0, result.output
    assert "Thread Detail" in result.output
    assert "First thread" in result.output
    assert "phase=context" in result.output


def test_hud_threads_picker_can_transition_into_stream(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads":
            return [
                {
                    "thread_id": "thread-2",
                    "topic": "Stream thread",
                    "protocol": "debate",
                    "current_phase": "context",
                    "status": "active",
                }
            ]
        if path == "/api/threads/thread-2":
            return {
                "thread_id": "thread-2",
                "topic": "Stream thread",
                "protocol": "debate",
                "current_phase": "context",
            }
        if path == "/api/threads/thread-2/status":
            return {
                "thread_id": "thread-2",
                "current_phase": "context",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)
    monkeypatch.setattr(main, "_resolve_bound_thread_id", lambda: None)

    def fake_sleep(_interval: float):
        raise KeyboardInterrupt

    monkeypatch.setattr(main.time, "sleep", fake_sleep)

    result = runner.invoke(app, ["hud", "--threads", "--stream"], input="1\n1\n")

    assert result.exit_code == 0, result.output
    assert "B-TWIN HUD stream" in result.output
    assert "thread=thread-2" in result.output
