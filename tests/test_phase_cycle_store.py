from btwin_core.phase_cycle_store import PhaseCycleStore


def test_phase_cycle_store_persists_cycle_state_per_thread(tmp_path):
    store = PhaseCycleStore(tmp_path)

    state = store.start_cycle(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review", "revise"],
    )

    assert state.cycle_index == 1
    assert store.read("thread-1") == state


def test_phase_cycle_store_advances_same_phase_retry_to_next_cycle(tmp_path):
    store = PhaseCycleStore(tmp_path)
    store.start_cycle(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review", "revise"],
    )

    next_state = store.finish_cycle(
        thread_id="thread-1",
        gate_outcome="retry",
        next_phase="review",
    )

    assert next_state.cycle_index == 2
    assert next_state.phase_name == "review"
    assert store.read("thread-1") == next_state
