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
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolSection, ProtocolStore, ProtocolTransition
from btwin_core.runtime_binding_store import RuntimeBindingStore
from btwin_core.thread_store import ThreadStore
from btwin_core.workflow_event_log import WorkflowEventLog


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


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
            phases=[
                ProtocolPhase(
                    name="review",
                    actions=["contribute"],
                    template=[ProtocolSection(section="completed", required=True)],
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
    assert "Current:" not in result.output
    assert "Last gate:" not in result.output


def test_standalone_phase_cycle_payload_prefers_protocol_keys_in_visuals(tmp_path, monkeypatch):
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
                        {"role": "reviewer", "action": "review", "alias": "Review", "key": "step-review"},
                        {"role": "implementer", "action": "revise", "alias": "Revise", "key": "step-revise"},
                    ],
                ),
                ProtocolPhase(name="decision", actions=["decide"]),
            ],
            transitions=[
                ProtocolTransition.model_validate(
                    {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "gate-retry"}
                ),
                ProtocolTransition.model_validate(
                    {"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate", "key": "gate-accept"}
                ),
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

    rendered = main._render_thread_watch(thread, status_summary, events)

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

    rendered = main._render_thread_watch(thread, status_summary, events)

    assert "[cyan]04:04:46  CODEX -> BTWIN  Phase attempt started[/cyan]" in rendered
    assert "[green]04:07:27  BTWIN -> CODEX  Required result recorded[/green]" in rendered


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
        "_render_hud",
        lambda thread_id, limit: "B-TWIN HUD\n" + "\n".join(f"line {i}" for i in range(12)),
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
    assert "Views" in result.output
    assert "threads" in result.output
    assert "Active Threads" in result.output
    assert "thread-1" in result.output
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
