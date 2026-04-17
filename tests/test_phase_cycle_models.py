from btwin_core.context_core import ContextCore
from btwin_core.phase_cycle import PhaseCycleState


def test_context_core_contains_reanchor_fields():
    payload = ContextCore(
        thread_goal="Ship the feature",
        phase_purpose="Review the current implementation",
        non_goals=["rewrite unrelated files"],
        required_result="review verdict",
        last_cycle_outcome="revisions requested",
        next_expected_role="implementer",
        next_expected_action="implement changes from the current review",
        current_cycle_index=2,
        current_step_label="revise",
        current_step_alias="Revise",
        current_step_role="implementer",
    ).model_dump()

    assert set(payload) >= {
        "thread_goal",
        "phase_purpose",
        "non_goals",
        "required_result",
        "last_cycle_outcome",
        "next_expected_role",
        "next_expected_action",
        "current_cycle_index",
        "current_step_label",
        "current_step_alias",
        "current_step_role",
    }


def test_phase_cycle_state_tracks_current_cycle_without_agent_binding():
    state = PhaseCycleState.start(
        thread_id="thread-123",
        phase_name="review",
        procedure_steps=["review", "revise", "review"],
    )

    assert state.thread_id == "thread-123"
    assert state.phase_name == "review"
    assert state.cycle_index == 1
    assert state.current_step_index == 0
    assert state.current_step_label == "review"
    assert state.completed_steps == []
    assert state.status == "active"
    assert state.last_gate_outcome is None
