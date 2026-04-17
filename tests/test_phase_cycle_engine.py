from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_engine import advance_phase_cycle
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolSection, ProtocolTransition


def _review_loop_protocol() -> Protocol:
    return Protocol(
        name="review-loop",
        phases=[
            ProtocolPhase(
                name="review",
                description="Review the implementation and request changes if needed.",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[
                    {
                        "role": "reviewer",
                        "action": "review",
                        "guidance": "Review the current implementation state.",
                    },
                    {
                        "role": "implementer",
                        "action": "revise",
                        "guidance": "Implement revisions from review feedback.",
                    },
                ],
            )
        ],
        transitions=[ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry"})],
        outcomes=["retry", "accept"],
    )


def _review_then_decide_protocol() -> Protocol:
    return Protocol(
        name="review-then-decide",
        phases=[
            ProtocolPhase(
                name="review",
                description="Review and revise until accepted.",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[
                    {
                        "role": "reviewer",
                        "action": "review",
                        "guidance": "Review the current implementation state.",
                    }
                ],
            ),
            ProtocolPhase(
                name="decision",
                description="Record the final decision.",
                actions=["decide"],
            ),
        ],
        transitions=[ProtocolTransition.model_validate({"from": "review", "to": "decision", "on": "accept"})],
        outcomes=["retry", "accept"],
    )


def test_engine_restarts_same_phase_when_transition_loops_back():
    result = advance_phase_cycle(
        thread={"thread_id": "thread-1", "topic": "Review loop thread"},
        protocol=_review_loop_protocol(),
        current_state=PhaseCycleState.start(
            thread_id="thread-1",
            phase_name="review",
            procedure_steps=["review", "revise"],
        ),
        outcome="retry",
    )

    assert result.next_state.cycle_index == 2
    assert result.next_state.phase_name == "review"


def test_engine_advances_to_next_phase_when_transition_changes_phase():
    result = advance_phase_cycle(
        thread={"thread_id": "thread-1", "topic": "Review then decide"},
        protocol=_review_then_decide_protocol(),
        current_state=PhaseCycleState.start(
            thread_id="thread-1",
            phase_name="review",
            procedure_steps=["review"],
        ),
        outcome="accept",
    )

    assert result.next_state.phase_name == "decision"


def test_engine_derives_next_expected_action_from_procedure_step():
    result = advance_phase_cycle(
        thread={"thread_id": "thread-1", "topic": "Review loop thread"},
        protocol=_review_loop_protocol(),
        current_state=PhaseCycleState.start(
            thread_id="thread-1",
            phase_name="review",
            procedure_steps=["review", "revise"],
        ),
        outcome="retry",
    )

    assert result.context_core.next_expected_action == "Review the current implementation state."
    assert result.context_core.next_expected_role == "reviewer"
    assert result.context_core.current_step_alias == "review"
    assert result.context_core.current_step_role == "reviewer"


def test_engine_uses_step_alias_and_role_when_available():
    protocol = Protocol(
        name="aliased-review-loop",
        phases=[
            ProtocolPhase(
                name="review",
                description="Review the implementation and request changes if needed.",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[
                    {
                        "role": "reviewer",
                        "action": "review",
                        "alias": "Review Pass",
                        "guidance": "Review the current implementation state.",
                    },
                    {
                        "role": "implementer",
                        "action": "revise",
                        "alias": "Revision Pass",
                        "guidance": "Implement revisions from review feedback.",
                    },
                ],
            )
        ],
        transitions=[ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry"})],
        outcomes=["retry", "accept"],
    )

    result = advance_phase_cycle(
        thread={"thread_id": "thread-1", "topic": "Review loop thread"},
        protocol=protocol,
        current_state=PhaseCycleState.start(
            thread_id="thread-1",
            phase_name="review",
            procedure_steps=["review", "revise"],
        ),
        outcome="retry",
    )

    assert result.context_core.current_step_label == "review"
    assert result.context_core.current_step_alias == "Review Pass"
    assert result.context_core.current_step_role == "reviewer"
    assert result.context_core.next_expected_role == "reviewer"
