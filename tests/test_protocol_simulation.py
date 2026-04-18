from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_engine import advance_phase_cycle, build_phase_cycle_context_core, phase_cycle_procedure_actions
from btwin_core.protocol_flow import describe_next
from btwin_core.protocol_store import Protocol
from btwin_core.workflow_constraints import evaluate_workflow_hook

from tests.protocol_scenario_matrix import (
    SCENARIO_IDS,
    get_scenario,
    scenario_protocol_definition,
)


def _thread(*, thread_id: str, protocol: str, phase: str, topic: str = "Scenario thread") -> dict[str, object]:
    return {
        "thread_id": thread_id,
        "protocol": protocol,
        "current_phase": phase,
        "topic": topic,
        "phase_participants": ["alice"],
        "participants": ["alice"],
    }


def _protocol(scenario_id: str) -> Protocol:
    return Protocol.model_validate(scenario_protocol_definition(scenario_id))


def test_protocol_scenario_matrix_exposes_stable_ids_and_shared_metadata():
    assert SCENARIO_IDS == (
        "happy_path_accept",
        "retry_same_phase",
        "blocked_stop_missing_contribution",
        "invalid_outcome_mapping",
        "close_path",
        "attach_seed_first_cycle",
    )

    retry = get_scenario("retry_same_phase")
    assert retry.protocol_name == "review-loop"
    assert retry.preview_status == "valid"
    assert retry.live_smoke_required is True
    assert retry.gate_key == "retry-loop"
    assert retry.procedure_key == "review-pass"
    assert retry.outcome == "retry"
    assert retry.target_phase == "review"
    assert retry.cycle_index_changes == ((1, 2), (2, 3))

    close = get_scenario("close_path")
    assert close.protocol_name == "review-loop"
    assert close.preview_status == "valid"
    assert close.live_smoke_required is True
    assert close.gate_key == "close-gate"
    assert close.procedure_key == "review-pass"
    assert close.outcome == "close"
    assert close.target_phase == "decision"
    assert close.cycle_index_changes == ((1, 1),)


def test_review_loop_scenarios_share_preview_and_simulation_vocabulary():
    protocol = _protocol("happy_path_accept")
    scenario = get_scenario("happy_path_accept")
    thread = _thread(thread_id="thread-1", protocol=protocol.name, phase="review")
    contributions = [{"agent": "alice", "phase": "review", "_content": "## completed\nReady.\n"}]

    plan = describe_next(thread, protocol, contributions, outcome=scenario.outcome)
    assert plan.error is None
    assert plan.suggested_action == "advance_phase"
    assert plan.next_phase == scenario.target_phase

    state = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=phase_cycle_procedure_actions(protocol.phases[0]),
    )
    next_state = advance_phase_cycle(
        thread=thread,
        protocol=protocol,
        current_state=state,
        outcome=scenario.outcome,
    ).next_state
    assert next_state.phase_name == "decision"
    assert next_state.cycle_index == 1


def test_retry_same_phase_simulation_advances_cycle_index_twice():
    protocol = _protocol("retry_same_phase")
    scenario = get_scenario("retry_same_phase")
    thread = _thread(thread_id="thread-1", protocol=protocol.name, phase="review")
    contributions = [{"agent": "alice", "phase": "review", "_content": "## completed\nNeeds another pass.\n"}]

    state = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=phase_cycle_procedure_actions(protocol.phases[0]),
    )
    first = advance_phase_cycle(
        thread=thread,
        protocol=protocol,
        current_state=state,
        outcome=scenario.outcome,
    )
    second = advance_phase_cycle(
        thread=thread,
        protocol=protocol,
        current_state=first.next_state,
        outcome=scenario.outcome,
    )

    assert first.next_state.cycle_index == scenario.cycle_index_changes[0][1]
    assert second.next_state.cycle_index == scenario.cycle_index_changes[1][1]
    assert first.context_core.current_step_alias == "Review"
    assert first.context_core.current_step_role == "reviewer"
    assert first.context_core.next_expected_role == "reviewer"
    assert second.context_core.current_cycle_index == 3
    assert second.context_core.current_step_alias == "Review"
    assert contributions[0]["_content"].startswith("## completed")


def test_blocked_stop_missing_contribution_reuses_shared_review_loop_fixture():
    protocol = _protocol("blocked_stop_missing_contribution")
    scenario = get_scenario("blocked_stop_missing_contribution")
    thread = _thread(thread_id="thread-1", protocol=protocol.name, phase="review")

    result = evaluate_workflow_hook(
        event="Stop",
        thread=thread,
        protocol=protocol,
        actor="alice",
        contributions=[],
    )

    assert scenario.preview_status == "note"
    assert scenario.live_smoke_required is True
    assert result.decision == "block"
    assert result.reason == "missing_contribution"
    assert result.required_result_recorded is False
    assert "baseline runtime guard remains always-on" in (result.overlay or "")


def test_invalid_outcome_mapping_fails_before_live_execution():
    protocol = _protocol("invalid_outcome_mapping")
    scenario = get_scenario("invalid_outcome_mapping")
    thread = _thread(thread_id="thread-1", protocol=protocol.name, phase="review")
    contributions = [{"agent": "alice", "phase": "review", "_content": "## completed\nReady.\n"}]

    plan = describe_next(thread, protocol, contributions, outcome="reject")

    assert scenario.preview_status == "invalid"
    assert scenario.live_smoke_required is False
    assert plan.error == "unsupported_outcome"
    assert plan.suggested_action == "record_outcome"
    assert plan.valid_outcomes == ["retry", "accept", "close"]


def test_close_path_uses_the_close_extension_fixture():
    protocol = _protocol("close_path")
    scenario = get_scenario("close_path")
    thread = _thread(thread_id="thread-1", protocol=protocol.name, phase="review")
    contributions = [{"agent": "alice", "phase": "review", "_content": "## completed\nClose it out.\n"}]

    plan = describe_next(thread, protocol, contributions, outcome=scenario.outcome)
    assert scenario.preview_status == "valid"
    assert plan.suggested_action == "advance_phase"
    assert plan.next_phase == "decision"
    transition = advance_phase_cycle(
        thread=thread,
        protocol=protocol,
        current_state=PhaseCycleState.start(
            thread_id="thread-1",
            phase_name="review",
            procedure_steps=phase_cycle_procedure_actions(protocol.phases[0]),
        ),
        outcome=scenario.outcome,
    )
    assert transition.next_state.phase_name == "decision"
    assert transition.next_state.cycle_index == 1


def test_attach_seed_first_cycle_exposes_seedable_cycle_state():
    protocol = _protocol("attach_seed_first_cycle")
    scenario = get_scenario("attach_seed_first_cycle")
    phase = protocol.phases[0]
    thread = _thread(thread_id="thread-1", protocol=protocol.name, phase="review", topic="Attached helper smoke")
    state = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name=phase.name,
        procedure_steps=phase_cycle_procedure_actions(phase),
    )

    context = build_phase_cycle_context_core(
        thread=thread,
        protocol=protocol,
        phase=phase,
        state=state,
    )

    assert scenario.preview_status == "valid"
    assert scenario.live_smoke_required is True
    assert state.cycle_index == 1
    assert context.current_cycle_index == 1
    assert context.current_step_alias == "Review"
    assert context.current_step_role == "reviewer"
    assert context.next_expected_role == "reviewer"
