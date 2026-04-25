from pathlib import Path

from fastapi.testclient import TestClient

from btwin_cli.api_threads import create_threads_router
from btwin_core.delegation_state import DelegationState
from btwin_core.delegation_store import DelegationStore
from btwin_core.event_bus import EventBus
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
from btwin_core.system_mailbox_store import SystemMailboxStore
from btwin_core.thread_store import ThreadStore


class _FakeAgentRunner:
    def __init__(self, active_threads_by_agent):
        self._active_threads_by_agent = active_threads_by_agent
        self.spawn_calls = []
        self.recover_calls = []
        self.attach_or_resume_calls = []
        self.session_status = None
        self.runtime_sessions_by_agent = None

    def list_active_threads_by_agent(self):
        return self._active_threads_by_agent

    def list_runtime_sessions_by_agent(self):
        if self.runtime_sessions_by_agent is None:
            return {}
        return self.runtime_sessions_by_agent

    def get_runtime_session_status(self, thread_id, agent_name):
        del thread_id, agent_name
        return self.session_status

    async def spawn_for_thread(self, thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        self.spawn_calls.append(
            {
                "thread_id": thread_id,
                "agent_name": agent_name,
                "bypass_permissions": bypass_permissions,
                "workspace_root": workspace_root,
            }
        )
        self._active_threads_by_agent.setdefault(agent_name, []).append(thread_id)
        return True

    async def recover_for_thread(self, thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        self.recover_calls.append(
            {
                "thread_id": thread_id,
                "agent_name": agent_name,
                "bypass_permissions": bypass_permissions,
                "workspace_root": workspace_root,
            }
        )
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "recoverable": True,
            "transport_mode": "live_process_transport",
            "recovery_started": True,
        }

    async def attach_or_resume_for_thread(self, thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        self.attach_or_resume_calls.append(
            {
                "thread_id": thread_id,
                "agent_name": agent_name,
                "bypass_permissions": bypass_permissions,
                "workspace_root": workspace_root,
            }
        )
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "primary_transport_mode": "live_process_transport",
            "transport_mode": "resume_invocation_transport",
            "recovery_started": True,
            "reused_session": False,
            "resumed_from_state": False,
        }


def _seed_waiting_delegate_thread(tmp_path: Path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-wait",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute", "review"],
                        "template": [{"section": "completed", "required": True}],
                        "outcome_policy": "review-outcomes",
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    },
                    {
                        "name": "followup",
                        "actions": ["contribute", "review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "implementer", "action": "revise", "alias": "Revise"},
                        ],
                    },
                ],
                "outcome_policies": [
                    {
                        "name": "review-outcomes",
                        "emitters": ["reviewer", "user"],
                        "actions": ["decide"],
                        "outcomes": ["retry", "accept"],
                    }
                ],
                "transitions": [
                    {"from": "review", "on": "retry", "to": "review", "alias": "Retry"},
                    {"from": "review", "on": "accept", "to": "followup", "alias": "Accept"},
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Delegate wait thread",
        protocol="delegate-wait",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(thread_store.data_dir).write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "review",
        content="## Completed\n\nInitial review finished.",
        tldr="review done",
    )
    DelegationStore(thread_store.data_dir).write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="waiting_for_human",
            updated_at="2026-04-20T00:00:00Z",
            loop_iteration=1,
            current_phase="review",
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent="alice",
            required_action="record_outcome",
            expected_output="record outcome: retry, accept",
            stop_reason="human_outcome_required",
        )
    )
    return thread_store, protocol_store, event_bus, thread


def test_threads_router_exposes_agent_inbox_and_agent_status(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()

    thread = thread_store.create_thread(
        topic="Attached API thread",
        protocol="debate",
        participants=["alice", "bob"],
        initial_phase="context",
    )
    thread_store.send_message(
        thread_id=thread["thread_id"],
        from_agent="bob",
        content="Please review this.",
        tldr="review request",
        delivery_mode="direct",
        target_agents=["alice"],
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    inbox_response = client.get(f"/api/threads/{thread['thread_id']}/inbox", params={"agent": "alice"})
    assert inbox_response.status_code == 200
    assert inbox_response.json()["pending_count"] == 1

    status_response = client.get(f"/api/threads/{thread['thread_id']}/status", params={"agent": "alice"})
    assert status_response.status_code == 200
    assert status_response.json()["participant_status"] == "joined"
    assert status_response.json()["pending_message_count"] == 1


def test_delegate_start_creates_running_delegation_state(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-review",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    }
                ],
            }
        )
    )

    thread = thread_store.create_thread(
        topic="Delegate thread",
        protocol="delegate-review",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(thread_store.data_dir).write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )

    event_queue = event_bus.subscribe()

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(f"/api/threads/{thread['thread_id']}/delegate/start")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["updated_at"]
    assert payload["target_role"] == "reviewer"
    assert payload["resolved_agent"] == "alice"
    assert payload["required_action"] == "submit_contribution"
    assert payload["expected_output"] == "review contribution"
    assert "reason_blocked" not in payload

    inbox_response = client.get(f"/api/threads/{thread['thread_id']}/inbox", params={"agent": "alice"})
    assert inbox_response.status_code == 200
    assert inbox_response.json()["pending_count"] == 1
    published_event = event_queue.get_nowait()
    assert published_event.type == "thread_updated"
    assert published_event.resource_id == thread["thread_id"]
    assert event_queue.qsize() == 0

    second_response = client.post(f"/api/threads/{thread['thread_id']}/delegate/start")
    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["updated_at"] > payload["updated_at"]
    assert second_payload["status"] == payload["status"]
    assert second_payload["target_role"] == payload["target_role"]
    assert second_payload["resolved_agent"] == payload["resolved_agent"]
    assert second_payload["required_action"] == payload["required_action"]
    assert second_payload["expected_output"] == payload["expected_output"]

    second_inbox_response = client.get(f"/api/threads/{thread['thread_id']}/inbox", params={"agent": "alice"})
    assert second_inbox_response.status_code == 200
    assert second_inbox_response.json()["pending_count"] == 1
    assert event_queue.qsize() == 0

    status_response = client.get(f"/api/threads/{thread['thread_id']}/delegate/status")
    assert status_response.status_code == 200
    assert status_response.json() == second_payload


def test_delegate_wait_returns_resume_packet(tmp_path):
    thread_store, protocol_store, event_bus, thread = _seed_waiting_delegate_thread(tmp_path)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.get(f"/api/threads/{thread['thread_id']}/delegate/wait")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "waiting_for_human"
    assert payload["thread"]["alias"] == thread["thread_id"]
    assert payload["protocol"]["phase"] == "review"
    assert payload["resume"]["target_role"] == "reviewer"
    assert payload["resume"]["resolved_agent"] == "alice"
    assert payload["resume"]["required_action"] == "record_outcome"
    assert payload["resume"]["why_now"] == "phase requirements are met and a human outcome is required to continue"
    assert payload["resume"]["token"]
    assert "delegate respond" in payload["resume"]["suggested_next_command"]


def test_delegate_respond_reenters_loop_after_human_outcome(tmp_path):
    thread_store, protocol_store, event_bus, thread = _seed_waiting_delegate_thread(tmp_path)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/delegate/respond",
        json={"outcome": "retry", "summary": "Need one more review pass."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["current_phase"] == "review"
    assert payload["current_cycle_index"] == 2
    assert payload["loop_iteration"] == 2
    assert payload["resolved_agent"] == "alice"

    status_response = client.get(f"/api/threads/{thread['thread_id']}/delegate/status")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "running"

    inbox = thread_store.list_inbox(thread["thread_id"], "alice")
    assert len(inbox) == 1
    assert "Need one more review pass." in inbox[0]["_content"]


def test_delegate_status_returns_blocked_reason_when_target_role_missing(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-review",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute"],
                        "template": [{"section": "completed", "required": True}],
                    }
                ],
            }
        )
    )

    thread = thread_store.create_thread(
        topic="Blocked delegate thread",
        protocol="delegate-review",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(thread_store.data_dir).write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(f"/api/threads/{thread['thread_id']}/delegate/start")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload.get("target_role") is None
    assert payload["reason_blocked"] == "missing_target_role"

    status_response = client.get(f"/api/threads/{thread['thread_id']}/delegate/status")
    assert status_response.status_code == 200
    assert status_response.json()["reason_blocked"] == "missing_target_role"


def test_delegate_start_uses_phase_cycle_fallback_when_thread_phase_missing(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-review",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    }
                ],
            }
        )
    )

    thread = thread_store.create_thread(
        topic="Fallback delegate thread",
        protocol="delegate-review",
        participants=["alice"],
        initial_phase=None,
    )
    PhaseCycleStore(thread_store.data_dir).write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(f"/api/threads/{thread['thread_id']}/delegate/start")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["target_role"] == "reviewer"
    assert payload["resolved_agent"] == "alice"
    assert client.get(f"/api/threads/{thread['thread_id']}/inbox", params={"agent": "alice"}).json()["pending_count"] == 1


def test_delegate_start_prefers_phase_cycle_state_over_stale_thread_phase(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-stale-phase",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    },
                    {
                        "name": "decision",
                        "actions": ["decide"],
                        "procedure": [
                            {"role": "decider", "action": "decide", "alias": "Decision"},
                        ],
                    },
                ],
            }
        )
    )

    thread = thread_store.create_thread(
        topic="Stale phase delegate thread",
        protocol="delegate-stale-phase",
        participants=["alice"],
        initial_phase="decision",
    )
    PhaseCycleStore(thread_store.data_dir).write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(f"/api/threads/{thread['thread_id']}/delegate/start")

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_role"] == "reviewer"
    assert payload["resolved_agent"] == "alice"
    assert client.get(f"/api/threads/{thread['thread_id']}/inbox", params={"agent": "alice"}).json()["pending_count"] == 1


def test_delegate_start_rejects_when_direct_routing_is_disallowed(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-direct-blocked",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    }
                ],
            }
        )
    )

    thread = thread_store.create_thread(
        topic="Direct routing blocked delegate thread",
        protocol="delegate-direct-blocked",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(thread_store.data_dir).write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(f"/api/threads/{thread['thread_id']}/delegate/start")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["status"] == "blocked"
    assert detail["reason_blocked"] == "direct_message_not_allowed_in_phase"
    assert client.get(f"/api/threads/{thread['thread_id']}/inbox", params={"agent": "alice"}).json()["pending_count"] == 0


def test_delegate_start_reports_dispatch_failure_without_false_success(tmp_path, monkeypatch):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-review",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    }
                ],
            }
        )
    )

    thread = thread_store.create_thread(
        topic="Dispatch failure delegate thread",
        protocol="delegate-review",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(thread_store.data_dir).write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )

    def _fail_send_message(*args, **kwargs):
        del args, kwargs
        return None

    monkeypatch.setattr(thread_store, "send_message", _fail_send_message)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(f"/api/threads/{thread['thread_id']}/delegate/start")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["status"] == "blocked"
    assert detail["reason_blocked"] == "dispatch_failed"
    assert client.get(f"/api/threads/{thread['thread_id']}/delegate/status").json()["reason_blocked"] == "dispatch_failed"
    assert client.get(f"/api/threads/{thread['thread_id']}/inbox", params={"agent": "alice"}).json()["pending_count"] == 0


def test_delegate_start_rejects_closed_thread(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-review",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    }
                ],
            }
        )
    )

    thread = thread_store.create_thread(
        topic="Closed delegate thread",
        protocol="delegate-review",
        participants=["alice"],
        initial_phase="review",
    )
    thread_store.close_thread(thread["thread_id"], summary="done")

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(f"/api/threads/{thread['thread_id']}/delegate/start")
    assert response.status_code == 404

    status_response = client.get(f"/api/threads/{thread['thread_id']}/delegate/status")
    assert status_response.status_code == 404


def test_threads_router_exposes_system_mailbox_reports(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()

    thread = thread_store.create_thread(
        topic="Mailbox thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    SystemMailboxStore(thread_store.data_dir).append_report(
        {
            "thread_id": thread["thread_id"],
            "report_type": "cycle_result",
            "audience": "monitoring",
            "summary": "Cycle complete",
            "created_at": "2026-04-17T00:00:00+00:00",
        }
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.get("/api/system-mailbox", params={"threadId": thread["thread_id"], "limit": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["reports"][0]["report_type"] == "cycle_result"
    assert payload["reports"][0]["audience"] == "monitoring"


def test_threads_router_exposes_phase_cycle_progress(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "debate",
                "guard_sets": [
                    {
                        "name": "review-default",
                        "guards": [
                            "phase_actor_eligibility",
                            "direct_target_eligibility",
                        ],
                    }
                ],
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute"],
                        "template": [{"section": "completed", "required": True}],
                        "guard_set": "review-default",
                        "outcome_policy": "review-outcomes",
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                            {"role": "implementer", "action": "revise", "alias": "Revise"},
                        ],
                    }
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
        topic="Cycle thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(thread_store.data_dir).write(
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

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.get(f"/api/threads/{thread['thread_id']}/phase-cycle")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["cycle_index"] == 2
    assert payload["state"]["procedure_steps"] == ["review", "revise"]
    assert payload["state"]["current_step_label"] == "revise"
    assert payload["context_core"]["next_expected_role"] == "implementer"
    assert payload["context_core"]["next_expected_action"] == "revise"
    assert payload["context_core"]["current_step_alias"] == "Revise"
    assert payload["context_core"]["current_step_role"] == "implementer"
    assert payload["context_core"]["guard_set"] == "review-default"
    assert payload["context_core"]["declared_guards"] == [
        "phase_actor_eligibility",
        "direct_target_eligibility",
    ]
    assert payload["context_core"]["outcome_policy"] == "review-outcomes"
    assert payload["context_core"]["outcome_emitters"] == ["reviewer", "user"]
    assert payload["context_core"]["outcome_actions"] == ["decide"]
    assert payload["context_core"]["policy_outcomes"] == ["retry", "accept"]
    assert payload["visual"]["procedure"][0]["label"] == "Review"
    assert payload["visual"]["procedure"][-1]["key"] == "review-gate"
    assert payload["visual"]["guards"] == [
        {
            "key": "phase_actor_eligibility",
            "label": "phase_actor_eligibility",
            "status": "declared",
        },
        {
            "key": "direct_target_eligibility",
            "label": "direct_target_eligibility",
            "status": "declared",
        },
    ]


def test_threads_router_preserves_last_cycle_outcome_after_phase_transition(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        Protocol(
            name="review-then-decision",
            phases=[
                ProtocolPhase(
                    name="review",
                    actions=["contribute"],
                    template=[ProtocolSection(section="completed", required=True)],
                    procedure=[
                        {"role": "reviewer", "action": "review", "alias": "Review"},
                    ],
                ),
                ProtocolPhase(
                    name="decision",
                    actions=["decide"],
                    procedure=[
                        {"role": "decider", "action": "decide", "alias": "Decision"},
                    ],
                ),
            ],
            transitions=[ProtocolTransition.model_validate({"from": "review", "to": "decision", "on": "accept"})],
        )
    )

    thread = thread_store.create_thread(
        topic="Transitioned cycle thread",
        protocol="review-then-decision",
        participants=["alice"],
        initial_phase="decision",
    )
    PhaseCycleStore(thread_store.data_dir).write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="decision",
            procedure_steps=["decide"],
        ).model_copy(
            update={
                "last_gate_outcome": None,
                "last_cycle_outcome": "accept",
            }
        )
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.get(f"/api/threads/{thread['thread_id']}/phase-cycle")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["phase_name"] == "decision"
    assert payload["context_core"]["last_cycle_outcome"] == "accept"


def test_agent_runtime_status_includes_helper_overlay_fields(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    agent_runner = _FakeAgentRunner({})
    agent_runner.runtime_sessions_by_agent = {
        "alice": [
            {
                "thread_id": "thread-1",
                "provider": "codex",
                "transport_mode": "resume_invocation_transport",
                "primary_transport_mode": "resume_invocation_transport",
                "workspace_root": "/tmp/project",
                "helper_launch_cwd": "/tmp/project/.btwin/helpers/alice/workspace",
            }
        ]
    }

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(
        thread_store,
        protocol_store,
        event_bus,
        agent_runner=agent_runner,
    ))
    client = TestClient(app)

    response = client.get("/api/agent-runtime-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["agents"]["alice"][0]["workspace_root"] == "/tmp/project"
    assert payload["agents"]["alice"][0]["helper_launch_cwd"] == "/tmp/project/.btwin/helpers/alice/workspace"


def test_attached_api_allows_direct_message_to_thread_participant_when_target_is_inactive(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()

    thread = thread_store.create_thread(
        topic="Attached API direct delivery",
        protocol="debate",
        participants=["alice", "bob"],
        initial_phase="context",
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_threads_router(
            thread_store,
            protocol_store,
            event_bus,
            agent_runner=_FakeAgentRunner({"bob": [thread["thread_id"]]}),
        )
    )
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/messages",
        json={
            "fromAgent": "bob",
            "content": "alice only",
            "tldr": "direct ask",
            "deliveryMode": "direct",
            "targetAgents": ["alice"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["delivery_mode"] == "direct"
    assert payload["target_agents"] == ["alice"]


def test_attached_api_rejects_direct_message_when_current_phase_disallows_chat(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        Protocol(
            name="decision-only",
            description="Decision-only phase",
            phases=[
                ProtocolPhase(
                    name="decision",
                    actions=["decide"],
                    decided_by="user",
                    template=[ProtocolSection(section="agreed_points", required=True)],
                )
            ],
        )
    )
    thread = thread_store.create_thread(
        topic="Attached API direct delivery",
        protocol="decision-only",
        participants=["user", "alice"],
        initial_phase="decision",
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/messages",
        json={
            "fromAgent": "user",
            "content": "alice only",
            "tldr": "direct ask",
            "deliveryMode": "direct",
            "targetAgents": ["alice"],
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "direct_message_not_allowed_in_phase"
    assert "decision" in detail["hint"]


def test_attached_api_rejects_contribution_submit_when_phase_mismatches_current_phase(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        Protocol(
            name="workflow-check",
            description="Contribution guard",
            phases=[
                ProtocolPhase(
                    name="context",
                    actions=["contribute"],
                    template=[ProtocolSection(section="background", required=True)],
                ),
                ProtocolPhase(
                    name="decision",
                    actions=["decide"],
                    decided_by="user",
                    template=[ProtocolSection(section="agreed_points", required=True)],
                ),
            ],
        )
    )
    thread = thread_store.create_thread(
        topic="Attached API contribution guard",
        protocol="workflow-check",
        participants=["user", "alice"],
        initial_phase="context",
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/contributions",
        json={
            "agentName": "alice",
            "phase": "decision",
            "content": "## agreed_points\nShip it.\n",
            "tldr": "decision made",
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "phase_mismatch"
    assert "context" in detail["hint"]


def test_spawn_agent_accepts_bypass_permissions_flag(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    agent_runner = _FakeAgentRunner({})

    thread = thread_store.create_thread(
        topic="Attached API spawn agent",
        protocol="debate",
        participants=["user"],
        initial_phase="context",
    )

    class _FakeAgentStore:
        def get_agent(self, name: str):
            if name == "alice":
                return {"name": "alice"}
            return None

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_threads_router(
            thread_store,
            protocol_store,
            event_bus,
            agent_store=_FakeAgentStore(),
            agent_runner=agent_runner,
        )
    )
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/spawn-agent",
        json={"agentName": "alice", "bypassPermissions": True, "projectRoot": "/tmp/test-project"},
    )

    assert response.status_code == 200
    assert agent_runner.attach_or_resume_calls == [
        {
            "thread_id": thread["thread_id"],
            "agent_name": "alice",
            "bypass_permissions": True,
            "workspace_root": Path("/tmp/test-project"),
        }
    ]


def test_spawn_agent_uses_attach_or_resume_for_recoverable_existing_session(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    agent_runner = _FakeAgentRunner({})
    agent_runner.session_status = {
        "thread_id": "thread-1",
        "agent_name": "alice",
        "primary_transport_mode": "live_process_transport",
        "transport_mode": "resume_invocation_transport",
        "recoverable": True,
        "degraded": True,
        "recovery_attempts": 1,
    }

    thread = thread_store.create_thread(
        topic="Attached API duplicate attach",
        protocol="debate",
        participants=["user", "alice"],
        initial_phase="context",
    )

    class _FakeAgentStore:
        def get_agent(self, name: str):
            if name == "alice":
                return {"name": "alice"}
            return None

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_threads_router(
            thread_store,
            protocol_store,
            event_bus,
            agent_store=_FakeAgentStore(),
            agent_runner=agent_runner,
        )
    )
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/spawn-agent",
        json={"agentName": "alice", "projectRoot": "/tmp/test-project"},
    )

    assert response.status_code == 200
    assert agent_runner.attach_or_resume_calls == [
        {
            "thread_id": thread["thread_id"],
            "agent_name": "alice",
            "bypass_permissions": None,
            "workspace_root": Path("/tmp/test-project"),
        }
    ]
    assert response.json()["recovery_started"] is True


def test_advance_phase_sets_phase_participants_from_next_procedure_roles(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "role-next",
                "phases": [
                    {
                        "name": "plan",
                        "actions": ["contribute"],
                        "template": [{"section": "plan", "required": True}],
                        "procedure": [{"role": "moderator", "action": "plan"}],
                    },
                    {
                        "name": "implement",
                        "actions": ["contribute"],
                        "template": [{"section": "implementation", "required": True}],
                        "procedure": [{"role": "developer", "action": "implement"}],
                    },
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Role next thread",
        protocol="role-next",
        participants=["moderator", "developer", "reviewer"],
        initial_phase="plan",
        phase_participants=["moderator"],
    )
    thread_store.submit_contribution(
        thread["thread_id"],
        "moderator",
        "plan",
        content="## plan\nReady.\n",
        tldr="plan ready",
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/advance-phase",
        json={"nextPhase": "implement"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_phase"] == "implement"
    assert payload["phase_participants"] == ["developer"]


def test_recover_agent_uses_agent_runner_recovery_path(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    agent_runner = _FakeAgentRunner({})

    thread = thread_store.create_thread(
        topic="Attached API recover agent",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )

    class _FakeAgentStore:
        def get_agent(self, name: str):
            if name == "alice":
                return {"name": "alice"}
            return None

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_threads_router(
            thread_store,
            protocol_store,
            event_bus,
            agent_store=_FakeAgentStore(),
            agent_runner=agent_runner,
        )
    )
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/recover-agent",
        json={"agentName": "alice", "bypassPermissions": True, "projectRoot": "/tmp/test-project"},
    )

    assert response.status_code == 200
    assert response.json()["recovery_started"] is True
    assert agent_runner.recover_calls == [
        {
            "thread_id": thread["thread_id"],
            "agent_name": "alice",
            "bypass_permissions": True,
            "workspace_root": Path("/tmp/test-project"),
        }
    ]
