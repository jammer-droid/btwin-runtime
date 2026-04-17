from btwin_core.phase_cycle import PhaseCycleState


def test_retry_outcome_starts_next_cycle_in_same_phase():
    state = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review", "revise"],
    )

    next_state = state.finish_cycle(gate_outcome="retry", next_phase="review")

    assert next_state.phase_name == "review"
    assert next_state.cycle_index == 2
    assert next_state.status == "active"
    assert next_state.last_gate_outcome == "retry"
    assert next_state.last_completed_at is not None


def test_accept_outcome_completes_phase_cycle_state():
    state = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review", "revise"],
    )

    completed = state.finish_cycle(gate_outcome="accept", next_phase="decision")

    assert completed.status == "completed"
    assert completed.last_gate_outcome == "accept"
    assert completed.cycle_index == 1
    assert completed.last_completed_at is not None


def test_stop_hook_block_does_not_advance_cycle_index():
    before = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review", "revise"],
    )

    after = before.record_local_recovery_block()

    assert before.cycle_index == after.cycle_index
    assert after.status == "blocked"
    assert after.last_gate_outcome is None
