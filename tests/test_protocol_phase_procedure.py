from btwin_core.protocol_flow import describe_next
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolProcedureStep, ProtocolSection, ProtocolStore, ProtocolTransition


def test_protocol_phase_can_define_role_agnostic_procedure_steps():
    proto = Protocol.model_validate(
        {
            "name": "review-loop",
            "phases": [
                {
                    "name": "review",
                    "actions": ["contribute"],
                    "template": [{"section": "completed", "required": True}],
                    "procedure": [
                        {"role": "reviewer", "action": "review"},
                        {"role": "implementer", "action": "revise"},
                        {"role": "reviewer", "action": "review"},
                    ],
                }
            ],
        }
    )

    assert proto.phases[0].procedure is not None
    assert proto.phases[0].procedure[0].role == "reviewer"
    assert proto.phases[0].procedure[0].action == "review"


def test_protocol_phase_and_gate_can_define_aliases_for_hud_display():
    proto = Protocol.model_validate(
        {
            "name": "review-loop",
            "phases": [
                {
                    "name": "review",
                    "actions": ["contribute"],
                    "procedure": [
                        {"role": "reviewer", "action": "review", "alias": "Review"},
                        {"role": "implementer", "action": "revise", "alias": "Revise"},
                    ],
                }
            ],
            "transitions": [
                {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate"},
            ],
        }
    )

    assert proto.phases[0].procedure is not None
    assert proto.phases[0].procedure[0].alias == "Review"
    assert proto.transitions[0].alias == "Retry Gate"


def test_protocol_phase_procedure_and_transition_aliases_round_trip_through_store(tmp_path):
    store = ProtocolStore(tmp_path / "protocols")
    store.save_protocol(
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
                )
            ],
            transitions=[ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate"})],
            outcomes=["retry", "accept"],
        )
    )

    proto = store.get_protocol("review-loop")

    assert proto is not None
    assert isinstance(proto.phases[0].procedure[0], ProtocolProcedureStep)
    assert proto.phases[0].procedure[0].alias == "Review"
    assert proto.phases[0].procedure[1].alias == "Revise"
    assert proto.transitions[0].alias == "Retry Gate"


def test_protocol_flow_can_restart_same_phase_for_next_cycle():
    protocol = Protocol(
        name="review-loop",
        phases=[
            ProtocolPhase(
                name="review",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[{"role": "reviewer", "action": "review"}],
            )
        ],
        transitions=[ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry"})],
        outcomes=["retry", "accept"],
    )

    thread = {
        "thread_id": "thread-1",
        "current_phase": "review",
        "phase_participants": ["alice"],
    }
    contributions = [
        {
            "agent": "alice",
            "phase": "review",
            "created_at": "2026-04-17T00:00:00+00:00",
            "_content": "## completed\nNeeds another pass.\n",
        }
    ]

    plan = describe_next(thread, protocol, contributions, outcome="retry")

    assert plan.next_phase == "review"
    assert plan.suggested_action == "advance_phase"


def test_protocol_flow_keeps_accept_as_non_loop_outcome():
    protocol = Protocol(
        name="review-loop",
        phases=[
            ProtocolPhase(
                name="review",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[{"role": "reviewer", "action": "review"}],
            )
        ],
        transitions=[ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry"})],
        outcomes=["retry", "accept"],
    )

    thread = {
        "thread_id": "thread-1",
        "current_phase": "review",
        "phase_participants": ["alice"],
    }
    contributions = [
        {
            "agent": "alice",
            "phase": "review",
            "created_at": "2026-04-17T00:00:00+00:00",
            "_content": "## completed\nReady to accept.\n",
        }
    ]

    plan = describe_next(thread, protocol, contributions, outcome="accept")

    assert plan.next_phase is None
    assert plan.suggested_action == "record_outcome"
