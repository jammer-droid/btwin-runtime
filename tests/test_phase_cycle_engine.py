from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_engine import advance_phase_cycle, build_phase_cycle_context_core
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
    assert result.context_core.current_step_key == "review"
    assert result.context_core.current_step_alias == "Review Pass"
    assert result.context_core.current_step_role == "reviewer"
    assert result.context_core.next_expected_role == "reviewer"


def test_engine_returns_trace_context_for_same_phase_retry():
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
                        "key": "review-pass",
                        "guidance": "Review the current implementation state.",
                    },
                    {
                        "role": "implementer",
                        "action": "revise",
                        "alias": "Revision Pass",
                        "key": "revise-pass",
                        "guidance": "Implement revisions from review feedback.",
                    },
                ],
            )
        ],
        transitions=[
            ProtocolTransition.model_validate(
                {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "retry-loop"}
            )
        ],
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

    assert result.trace_context.cycle_index == 1
    assert result.trace_context.next_cycle_index == 2
    assert result.trace_context.outcome == "retry"
    assert result.trace_context.procedure_key == "review-pass"
    assert result.trace_context.procedure_alias == "Review Pass"
    assert result.trace_context.gate_key == "retry-loop"
    assert result.trace_context.gate_alias == "Retry Gate"
    assert result.trace_context.target_phase == "review"


def test_engine_returns_trace_context_for_cross_phase_accept():
    protocol = Protocol(
        name="review-then-decide",
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
                        "key": "review-pass",
                        "guidance": "Review the current implementation state.",
                    }
                ],
            ),
            ProtocolPhase(name="decision", description="Record the decision.", actions=["decide"]),
        ],
        transitions=[
            ProtocolTransition.model_validate(
                {"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate", "key": "accept-gate"}
            )
        ],
        outcomes=["retry", "accept"],
    )

    result = advance_phase_cycle(
        thread={"thread_id": "thread-1", "topic": "Review loop thread"},
        protocol=protocol,
        current_state=PhaseCycleState.start(
            thread_id="thread-1",
            phase_name="review",
            procedure_steps=["review"],
        ),
        outcome="accept",
    )

    assert result.trace_context.cycle_index == 1
    assert result.trace_context.next_cycle_index == 1
    assert result.trace_context.outcome == "accept"
    assert result.trace_context.procedure_key == "review-pass"
    assert result.trace_context.procedure_alias == "Review Pass"
    assert result.trace_context.gate_key == "accept-gate"
    assert result.trace_context.gate_alias == "Accept Gate"
    assert result.trace_context.target_phase == "decision"


def test_context_core_uses_current_step_index_when_action_labels_repeat():
    protocol = Protocol(
        name="dual-review",
        phases=[
            ProtocolPhase(
                name="review",
                description="Two review passes with the same action label.",
                actions=["contribute"],
                procedure=[
                    {
                        "role": "reviewer",
                        "action": "review",
                        "alias": "Reviewer Pass",
                        "guidance": "Reviewer inspects the change.",
                    },
                    {
                        "role": "approver",
                        "action": "review",
                        "alias": "Approval Pass",
                        "guidance": "Approver checks the final revision.",
                    },
                ],
            )
        ],
    )

    state = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review", "review"],
    ).model_copy(
        update={
            "current_step_index": 1,
            "current_step_label": "review",
        }
    )

    context_core = build_phase_cycle_context_core(
        thread={"thread_id": "thread-1", "topic": "Dual review thread"},
        protocol=protocol,
        phase=protocol.phases[0],
        state=state,
    )

    assert context_core.current_step_label == "review"
    assert context_core.current_step_alias == "Approval Pass"
    assert context_core.current_step_role == "approver"
