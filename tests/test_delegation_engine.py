from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.protocol_store import Protocol, ProtocolOutcomePolicy, ProtocolPhase, ProtocolSection

from btwin_core.delegation_engine import (
    build_delegation_assignment,
    build_subagent_spawn_packet,
    default_phase_participants,
    role_fulfillment_participant_violation,
)


def _review_protocol() -> Protocol:
    return Protocol(
        name="review-loop",
        outcome_policies=[
            ProtocolOutcomePolicy(
                name="review-outcomes",
                emitters=["reviewer"],
                actions=["decide"],
                outcomes=["retry", "accept"],
            )
        ],
        phases=[
            ProtocolPhase(
                name="review",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[{"role": "reviewer", "action": "review"}],
                outcome_policy="review-outcomes",
            )
        ],
        outcomes=["retry", "accept"],
    )


def _review_thread() -> dict[str, object]:
    return {
        "thread_id": "thread-1",
        "protocol": "review-loop",
        "current_phase": "review",
        "phase_participants": ["alice"],
    }


def _review_cycle_state() -> PhaseCycleState:
    return PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review"],
    )


def test_build_delegation_assignment_uses_compiled_phase_and_role():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
    )

    assert assignment.status == "running"
    assert assignment.target_role == "reviewer"
    assert assignment.resolved_agent == "alice"
    assert assignment.required_action == "submit_contribution"
    assert assignment.expected_output == "review contribution"


def test_build_delegation_assignment_blocks_foreground_subagent_role_fulfillment():
    protocol = Protocol.model_validate(
        {
            **_review_protocol().model_dump(),
            "role_fulfillment": {
                "reviewer": {
                    "mode": "foreground_subagent",
                    "parent": "foreground",
                    "profile": "strict_reviewer",
                    "subagent_type": "explorer",
                }
            },
            "subagent_profiles": {
                "strict_reviewer": {
                    "description": "Find correctness and regression risks",
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": "medium",
                    "persona": "Find correctness risks first.",
                    "tools": {"allow": ["read_files", "run_tests"], "deny": ["edit_files"]},
                    "context": {"include": ["phase_contract", "recent_contributions"]},
                }
            },
        }
    )

    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=protocol,
        phase_cycle_state=_review_cycle_state(),
        role_bindings={},
    )

    assert assignment.status == "blocked"
    assert assignment.target_role == "reviewer"
    assert assignment.resolved_agent is None
    assert assignment.fulfillment_mode == "foreground_subagent"
    assert assignment.parent_executor == "foreground"
    assert assignment.subagent_profile == "strict_reviewer"
    assert assignment.subagent_type == "explorer"
    assert assignment.reason_blocked == "foreground_subagent_requires_managed_parent"
    assert assignment.stop_reason == "foreground_subagent_requires_managed_parent"


def test_build_delegation_assignment_resolves_managed_subagent_parent_without_role_binding():
    protocol = Protocol.model_validate(
        {
            **_review_protocol().model_dump(),
            "role_fulfillment": {
                "reviewer": {
                    "mode": "managed_agent_subagent",
                    "parent": "review_parent",
                    "profile": "strict_reviewer",
                    "subagent_type": "explorer",
                }
            },
            "subagent_profiles": {
                "strict_reviewer": {
                    "description": "Find correctness and regression risks",
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": "medium",
                    "persona": "Find correctness risks first.",
                    "tools": {"allow": ["read_files", "run_tests"], "deny": ["edit_files"]},
                    "context": {"include": ["phase_contract", "recent_contributions"]},
                }
            },
        }
    )

    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=protocol,
        phase_cycle_state=_review_cycle_state(),
        role_bindings={},
    )

    assert assignment.status == "running"
    assert assignment.target_role == "reviewer"
    assert assignment.resolved_agent == "review_parent"
    assert assignment.fulfillment_mode == "managed_agent_subagent"
    assert assignment.parent_executor == "review_parent"
    assert assignment.subagent_profile == "strict_reviewer"
    assert assignment.subagent_type == "explorer"
    assert assignment.executor_id == "thread-1:review:1:reviewer:strict_reviewer"


def test_build_subagent_spawn_packet_includes_parent_ready_codex_spawn_request():
    protocol = Protocol.model_validate(
        {
            **_review_protocol().model_dump(),
            "role_fulfillment": {
                "reviewer": {
                    "mode": "managed_agent_subagent",
                    "parent": "review_parent",
                    "profile": "strict_reviewer",
                    "subagent_type": "explorer",
                }
            },
            "subagent_profiles": {
                "strict_reviewer": {
                    "description": "Find correctness and regression risks",
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": "medium",
                    "persona": "Find correctness risks first.",
                    "tools": {"allow": ["read_files"], "deny": ["edit_files"]},
                    "context": {"include": ["phase_contract"]},
                }
            },
        }
    )
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=protocol,
        phase_cycle_state=_review_cycle_state(),
        role_bindings={},
    )

    packet = build_subagent_spawn_packet(
        thread=_review_thread(),
        protocol=protocol,
        phase_cycle_state=_review_cycle_state(),
        assignment=assignment,
    )

    assert packet is not None
    spawn_request = packet["codex_spawn_request"]
    assert spawn_request["agent_type"] == "explorer"
    assert spawn_request["model"] == "gpt-5.4-mini"
    assert spawn_request["reasoning_effort"] == "medium"
    assert spawn_request["fork_context"] is False
    assert "Find correctness risks first." in spawn_request["message"]
    assert "Required action: submit_contribution" in spawn_request["message"]
    assert "Expected output: review contribution" in spawn_request["message"]
    assert "--agent review_parent" in spawn_request["message"]
    assert "--executor-type managed_agent_subagent" in spawn_request["message"]
    assert "--parent-executor review_parent" in spawn_request["message"]
    assert "Do not invent a separate B-TWIN agent identity" in spawn_request["message"]


def test_default_phase_participants_prefers_agent_names_matching_procedure_roles():
    phase = ProtocolPhase(
        name="implement",
        actions=["contribute"],
        procedure=[{"role": "developer", "action": "implement"}],
    )
    thread = {
        "participants": [
            {"name": "moderator"},
            {"name": "developer"},
            {"name": "reviewer"},
        ],
        "phase_participants": ["moderator"],
    }

    assert default_phase_participants(thread, phase) == ["developer"]


def test_default_phase_participants_prefers_role_fulfillment_parent_for_custom_role():
    phase = ProtocolPhase(
        name="review",
        actions=["contribute"],
        procedure=[{"role": "reviewer", "action": "review"}],
    )
    protocol = Protocol.model_validate(
        {
            "name": "custom-review",
            "phases": [phase.model_dump()],
            "role_fulfillment": {
                "reviewer": {
                    "mode": "managed_agent_subagent",
                    "parent": "planner",
                    "profile": "strict_reviewer",
                    "subagent_type": "explorer",
                }
            },
            "subagent_profiles": {
                "strict_reviewer": {"description": "Review risks"},
            },
        }
    )
    thread = {
        "participants": [{"name": "moderator"}, {"name": "planner"}],
        "phase_participants": ["moderator"],
    }

    assert default_phase_participants(thread, phase, protocol=protocol) == ["planner"]


def test_role_fulfillment_participant_violation_reports_missing_parent():
    phase = ProtocolPhase(
        name="review",
        actions=["contribute"],
        procedure=[{"role": "reviewer", "action": "review"}],
    )
    protocol = Protocol.model_validate(
        {
            "name": "custom-review",
            "phases": [phase.model_dump()],
            "role_fulfillment": {
                "reviewer": {
                    "mode": "managed_agent_subagent",
                    "parent": "planner",
                    "profile": "strict_reviewer",
                    "subagent_type": "explorer",
                }
            },
            "subagent_profiles": {
                "strict_reviewer": {"description": "Review risks"},
            },
        }
    )
    thread = {
        "thread_id": "thread-1",
        "participants": [{"name": "moderator"}],
        "phase_participants": ["moderator"],
    }

    violation = role_fulfillment_participant_violation(thread, phase, protocol)

    assert violation is not None
    assert violation["error"] == "role_fulfillment_participant_missing"
    assert violation["role"] == "reviewer"
    assert violation["participant_kind"] == "parent"
    assert violation["participant"] == "planner"
    assert "Add --participant planner" in violation["hint"]


def test_build_delegation_assignment_blocks_when_role_binding_missing():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={},
    )

    assert assignment.status == "blocked"
    assert assignment.target_role == "reviewer"
    assert assignment.required_action == "submit_contribution"
    assert assignment.reason_blocked == "missing_role_binding"


def test_build_delegation_assignment_waits_for_human_when_outcome_is_required():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        contributions=[
            {
                "agent": "alice",
                "phase": "review",
                "created_at": "2026-04-20T00:00:00+00:00",
                "_content": "## completed\nReady for a decision.\n",
            }
        ],
    )

    assert assignment.status == "waiting_for_human"
    assert assignment.required_action == "record_outcome"
    assert assignment.expected_output is not None
    assert "retry" in assignment.expected_output
    assert "accept" in assignment.expected_output


def test_build_delegation_assignment_marks_completed_when_no_next_work_remains():
    protocol = Protocol(
        name="single-pass",
        phases=[
            ProtocolPhase(
                name="review",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[{"role": "reviewer", "action": "review"}],
            )
        ],
    )

    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=protocol,
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        contributions=[
            {
                "agent": "alice",
                "phase": "review",
                "created_at": "2026-04-20T00:00:00+00:00",
                "_content": "## completed\nLooks good.\n",
            }
        ],
    )

    assert assignment.status == "completed"
    assert assignment.required_action == "close_thread"


def test_build_delegation_assignment_blocks_when_runtime_recovery_has_failed():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        runtime_session={
            "degraded": False,
            "recoverable": False,
            "recovery_pending": False,
            "status": "failed",
            "transport_mode": "live_process_transport",
        },
    )

    assert assignment.status == "blocked"
    assert assignment.target_role == "reviewer"
    assert assignment.resolved_agent == "alice"
    assert assignment.reason_blocked == "failed_recovery"
    assert assignment.stop_reason == "failed_recovery"


def test_build_delegation_assignment_keeps_running_for_failed_exec_helper():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        runtime_session={
            "degraded": False,
            "recoverable": False,
            "recovery_pending": False,
            "status": "failed",
            "transport_mode": "resume_invocation_transport",
        },
    )

    assert assignment.status == "running"
    assert assignment.resolved_agent == "alice"


def test_build_delegation_assignment_keeps_running_for_recoverable_degraded_timeout():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        runtime_session={
            "degraded": True,
            "recoverable": True,
            "recovery_pending": False,
            "status": "received",
            "last_transport_error": "live transport timed out after 180.00s",
        },
    )

    assert assignment.status == "running"
    assert assignment.target_role == "reviewer"
    assert assignment.resolved_agent == "alice"


def test_build_delegation_assignment_does_not_treat_degraded_received_session_as_failed():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        runtime_session={
            "degraded": True,
            "recoverable": False,
            "recovery_pending": False,
            "status": "received",
        },
    )

    assert assignment.status == "running"
    assert assignment.resolved_agent == "alice"


def test_build_delegation_assignment_fails_when_loop_iteration_exceeds_cap():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        loop_iteration=2,
        max_auto_iterations=1,
    )

    assert assignment.status == "failed"
    assert assignment.target_role == "reviewer"
    assert assignment.resolved_agent == "alice"
    assert assignment.required_action == "submit_contribution"
    assert assignment.stop_reason == "max_auto_iterations_reached"
