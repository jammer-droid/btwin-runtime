import pytest

from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_engine import advance_phase_cycle, build_phase_cycle_context_core, phase_cycle_procedure_actions
from btwin_core.protocol_flow import describe_next
from btwin_core.protocol_store import Protocol, compile_protocol_definition
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
    return compile_protocol_definition(scenario_protocol_definition(scenario_id))


def _seed_review_state(protocol: Protocol) -> PhaseCycleState:
    return PhaseCycleState.start(
        thread_id="thread-1",
        phase_name=protocol.phases[0].name,
        procedure_steps=phase_cycle_procedure_actions(protocol.phases[0]),
    )


def _assert_trace_checkpoint(
    *,
    protocol: Protocol,
    thread: dict[str, object],
    state: PhaseCycleState,
    checkpoint,
) -> PhaseCycleState:
    phase = protocol.phases[0]
    target_phase = next((item for item in protocol.phases if item.name == checkpoint.target_phase), None)
    expected = checkpoint.as_dict()
    contributions = [{"agent": "alice", "phase": phase.name, "_content": "## completed\nReady.\n"}]
    expected_transition = next(
        (
            transition
            for transition in protocol.transitions
            if transition.from_phase == phase.name and transition.on == checkpoint.outcome
        ),
        None,
    )

    expected_procedure_key = phase.procedure[0].visual_key() if phase.procedure else None
    expected_gate_key = expected_transition.visual_key() if expected_transition is not None else None

    assert expected["label"] == checkpoint.label
    assert expected["procedure_key"] == expected_procedure_key
    assert expected["gate_key"] == expected_gate_key

    plan = describe_next(thread, protocol, contributions, outcome=checkpoint.outcome)
    assert plan.error is None
    assert plan.requested_outcome == expected["outcome"]
    assert plan.next_phase == expected["target_phase"]
    assert plan.suggested_action == "advance_phase"

    transition = advance_phase_cycle(
        thread=thread,
        protocol=protocol,
        current_state=state,
        outcome=checkpoint.outcome,
    )

    assert transition.next_state.phase_name == expected["target_phase"]
    assert transition.next_state.cycle_index == expected["next_cycle_index"]
    assert transition.next_state.last_cycle_outcome == expected["outcome"]
    assert transition.context_core.current_cycle_index == expected["next_cycle_index"]

    if expected["target_phase"] == state.phase_name:
        assert transition.next_state.last_gate_outcome == expected["outcome"]
    else:
        assert transition.next_state.last_gate_outcome is None

    if target_phase is not None and target_phase.procedure:
        first_step = target_phase.procedure[0]
        assert transition.context_core.current_step_alias == first_step.visual_label()
        assert transition.context_core.current_step_role == first_step.role
        assert transition.context_core.next_expected_role == first_step.role
        assert transition.context_core.next_expected_action == (first_step.guidance or first_step.action)

    return transition.next_state


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
    assert retry.outcome_policy == "review-outcomes"
    assert retry.outcome_emitters == ("reviewer", "user")
    assert retry.outcome_actions == ("decide",)
    assert retry.policy_outcomes == ("retry", "accept", "close")

    close = get_scenario("close_path")
    assert close.protocol_name == "review-loop"
    assert close.preview_status == "valid"
    assert close.live_smoke_required is True
    assert close.gate_key == "close-gate"
    assert close.procedure_key == "review-pass"
    assert close.outcome == "close"
    assert close.target_phase == "decision"
    assert close.cycle_index_changes == ((1, 1),)
    assert close.outcome_policy == "review-outcomes"
    assert close.outcome_emitters == ("reviewer", "user")
    assert close.outcome_actions == ("decide",)
    assert close.policy_outcomes == ("retry", "accept", "close")


def test_review_loop_shared_fixture_uses_authoring_dsl_and_compiles_outcome_policy_hints():
    definition = scenario_protocol_definition("retry_same_phase")

    assert definition["phases"][0]["gate"] == "review-gate"
    assert definition["phases"][0]["outcome_policy"] == "review-outcomes"
    assert definition["gates"][0]["name"] == "review-gate"
    assert definition["outcome_policies"][0]["name"] == "review-outcomes"

    protocol = compile_protocol_definition(definition)
    phase = protocol.phases[0]

    assert [transition.on for transition in protocol.transitions] == ["retry", "accept", "close"]
    assert phase.outcome_policy == "review-outcomes"
    assert phase.outcome_emitters == ["reviewer", "user"]
    assert phase.outcome_actions == ["decide"]
    assert phase.policy_outcomes == ["retry", "accept", "close"]


@pytest.mark.parametrize("scenario_id", ("happy_path_accept", "close_path"))
def test_review_loop_scenarios_share_preview_and_simulation_vocabulary(scenario_id: str):
    protocol = _protocol(scenario_id)
    scenario = get_scenario(scenario_id)
    thread = _thread(thread_id="thread-1", protocol=protocol.name, phase="review")
    checkpoint = scenario.simulation_trace[0]

    next_state = _assert_trace_checkpoint(
        protocol=protocol,
        thread=thread,
        state=_seed_review_state(protocol),
        checkpoint=checkpoint,
    )
    assert next_state.phase_name == checkpoint.target_phase
    assert next_state.cycle_index == checkpoint.next_cycle_index


def test_retry_same_phase_simulation_advances_cycle_index_twice():
    protocol = _protocol("retry_same_phase")
    scenario = get_scenario("retry_same_phase")
    thread = _thread(thread_id="thread-1", protocol=protocol.name, phase="review")
    state = _seed_review_state(protocol)

    for checkpoint in scenario.simulation_trace:
        state = _assert_trace_checkpoint(
            protocol=protocol,
            thread=thread,
            state=state,
            checkpoint=checkpoint,
        )

    assert [change[1] for change in scenario.cycle_index_changes] == [2, 3]


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
    checkpoint = scenario.simulation_trace[0]
    contributions = [{"agent": "alice", "phase": "review", "_content": "## completed\nReady.\n"}]

    plan = describe_next(thread, protocol, contributions, outcome=checkpoint.outcome)

    assert scenario.preview_status == "invalid"
    assert scenario.live_smoke_required is False
    assert checkpoint.as_dict()["outcome"] == "reject"
    assert plan.error == "unsupported_outcome"
    assert plan.suggested_action == "record_outcome"
    assert plan.valid_outcomes == ["retry", "accept", "close"]
    assert "retry | accept | close" in (plan.hint or "")

    with pytest.raises(ValueError, match="unsupported outcome"):
        advance_phase_cycle(
            thread=thread,
            protocol=protocol,
            current_state=_seed_review_state(protocol),
            outcome=checkpoint.outcome,
        )


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
