import pytest

from btwin_core.phase_cycle_engine import phase_cycle_procedure_actions
from btwin_core.protocol_flow import describe_next
from btwin_core.protocol_store import (
    Protocol,
    ProtocolAuthoringGate,
    ProtocolAuthoringGateRoute,
    ProtocolGuardSet,
    ProtocolOutcomePolicy,
    ProtocolPhase,
    ProtocolProcedureStep,
    ProtocolSection,
    ProtocolStore,
    ProtocolTransition,
    compile_protocol_definition,
)
from btwin_core.phase_cycle import PhaseCycleState
from btwin_cli.api_threads import _build_phase_cycle_visual
from btwin_cli.main import _phase_cycle_visual_payload

from tests.protocol_scenario_matrix import get_scenario, scenario_protocol_definition


def _expected_phase_cycle_visual_payload(scenario_id: str) -> dict[str, object]:
    scenario = get_scenario(scenario_id)
    return {
        "procedure": [
            *[step.as_dict() for step in scenario.visual_procedure],
            {"key": "gate", "label": "Gate", "status": "pending"},
        ],
        "gates": [gate.as_dict() for gate in scenario.visual_gates],
        "guards": [],
    }


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


def test_protocol_phase_procedure_and_transition_keys_round_trip_through_store(tmp_path):
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
                        {"role": "reviewer", "action": "review", "alias": "Review", "key": "step-review"},
                        {"role": "implementer", "action": "revise", "alias": "Revise", "key": "step-revise"},
                    ],
                )
            ],
            transitions=[
                ProtocolTransition.model_validate(
                    {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "gate-retry"}
                )
            ],
            outcomes=["retry", "accept"],
        )
    )

    proto = store.get_protocol("review-loop")

    assert proto is not None
    assert proto.phases[0].procedure[0].key == "step-review"
    assert proto.phases[0].procedure[1].key == "step-revise"
    assert proto.transitions[0].key == "gate-retry"


def test_protocol_guard_sets_and_phase_guard_set_round_trip_through_store(tmp_path):
    store = ProtocolStore(tmp_path / "protocols")
    store.save_protocol(
        Protocol(
            name="review-loop",
            guard_sets=[
                ProtocolGuardSet(
                    name="review-guards",
                    description="Guard set for the review phase.",
                    guards=["contribution_required", "phase_actor_eligibility"],
                )
            ],
            phases=[
                ProtocolPhase(
                    name="review",
                    actions=["contribute"],
                    guard_set="review-guards",
                    template=[ProtocolSection(section="completed", required=True)],
                    procedure=[
                        {"role": "reviewer", "action": "review", "alias": "Review"},
                        {"role": "implementer", "action": "revise", "alias": "Revise"},
                    ],
                )
            ],
            transitions=[
                ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry"}),
                ProtocolTransition.model_validate({"from": "review", "to": "decision", "on": "accept"}),
            ],
            outcomes=["retry", "accept"],
        )
    )

    proto = store.get_protocol("review-loop")

    assert proto is not None
    assert proto.guard_sets[0].name == "review-guards"
    assert proto.guard_sets[0].description == "Guard set for the review phase."
    assert proto.guard_sets[0].guards == ["contribution_required", "phase_actor_eligibility"]
    assert proto.phases[0].guard_set == "review-guards"


def test_protocol_authoring_gate_and_outcome_policy_round_trip_through_store(tmp_path):
    store = ProtocolStore(tmp_path / "protocols")
    store.save_protocol(
        Protocol(
            name="review-loop",
            gates=[
                ProtocolAuthoringGate(
                    name="review-gate",
                    description="Authoring-only gate definition for the review phase.",
                    routes=[
                        ProtocolAuthoringGateRoute(
                            outcome="retry",
                            target_phase="review",
                            alias="Retry Loop",
                            key="retry-loop",
                        ),
                        ProtocolAuthoringGateRoute(
                            outcome="accept",
                            target_phase="decision",
                            alias="Accept Gate",
                            key="accept-gate",
                        ),
                    ],
                )
            ],
            outcome_policies=[
                ProtocolOutcomePolicy(
                    name="review-outcomes",
                    description="Authoring-only outcome emission policy for review.",
                    emitters=["reviewer", "user"],
                    actions=["decide"],
                    outcomes=["retry", "accept"],
                )
            ],
            phases=[
                ProtocolPhase(
                    name="review",
                    actions=["contribute"],
                    gate="review-gate",
                    outcome_policy="review-outcomes",
                    template=[ProtocolSection(section="completed", required=True)],
                    procedure=[
                        {"role": "reviewer", "action": "review", "alias": "Review"},
                        {"role": "implementer", "action": "revise", "alias": "Revise"},
                    ],
                ),
                ProtocolPhase(name="decision", actions=["decide"]),
            ],
            transitions=[
                ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry"}),
                ProtocolTransition.model_validate({"from": "review", "to": "decision", "on": "accept"}),
            ],
            outcomes=["retry", "accept"],
        )
    )

    proto = store.get_protocol("review-loop")

    assert proto is not None
    assert proto.gates[0].authoring_only is True
    assert proto.gates[0].routes[0].outcome == "retry"
    assert proto.gates[0].routes[0].target_phase == "review"
    assert proto.outcome_policies[0].authoring_only is True
    assert proto.outcome_policies[0].emitters == ["reviewer", "user"]
    assert proto.phases[0].gate == "review-gate"
    assert proto.phases[0].outcome_policy == "review-outcomes"


def test_api_phase_cycle_visual_prefers_protocol_keys_and_aliases():
    protocol = Protocol(
        name="review-loop",
        phases=[
            ProtocolPhase(
                name="review",
                actions=["contribute"],
                procedure=[
                    {"role": "reviewer", "action": "review", "alias": "Review", "key": "step-review"},
                    {"role": "implementer", "action": "revise", "alias": "Revise", "key": "step-revise"},
                ],
            ),
            ProtocolPhase(name="decision", actions=["decide"]),
        ],
        transitions=[
            ProtocolTransition.model_validate(
                {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "gate-retry"}
            ),
            ProtocolTransition.model_validate(
                {"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate", "key": "gate-accept"}
            ),
        ],
        outcomes=["retry", "accept"],
    )
    phase = protocol.phases[0]
    state = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review", "revise"],
    ).model_copy(
        update={
            "cycle_index": 2,
            "current_step_label": "review",
            "last_gate_outcome": "retry",
        }
    )

    visual = _build_phase_cycle_visual(protocol=protocol, phase=phase, state=state)

    assert visual["procedure"][0] == {"key": "step-review", "label": "Review", "status": "active"}
    assert visual["procedure"][1] == {"key": "step-revise", "label": "Revise", "status": "pending"}
    assert visual["gates"][0] == {
        "key": "gate-retry",
        "label": "Retry Gate",
        "status": "completed",
        "target_phase": "review",
    }
    assert visual["gates"][1] == {
        "key": "gate-accept",
        "label": "Accept Gate",
        "status": "pending",
        "target_phase": "decision",
    }


def test_api_phase_cycle_visual_uses_step_index_for_repeated_actions():
    protocol = Protocol(
        name="review-loop",
        phases=[
            ProtocolPhase(
                name="review",
                actions=["contribute"],
                procedure=[
                    {"role": "reviewer", "action": "review", "alias": "Review 1", "key": "step-review-1"},
                    {"role": "implementer", "action": "revise", "alias": "Revise", "key": "step-revise"},
                    {"role": "reviewer", "action": "review", "alias": "Review 2", "key": "step-review-2"},
                ],
            ),
        ],
        transitions=[
            ProtocolTransition.model_validate(
                {"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate", "key": "gate-retry"}
            ),
        ],
        outcomes=["retry", "accept"],
    )
    phase = protocol.phases[0]
    state = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review", "revise", "review"],
    ).model_copy(
        update={
            "current_step_index": 2,
            "current_step_label": "review",
            "completed_steps": ["review", "revise"],
        }
    )

    visual = _build_phase_cycle_visual(protocol=protocol, phase=phase, state=state)

    assert visual["procedure"][0] == {
        "key": "step-review-1",
        "label": "Review 1",
        "status": "completed",
    }
    assert visual["procedure"][1] == {
        "key": "step-revise",
        "label": "Revise",
        "status": "completed",
    }
    assert visual["procedure"][2] == {
        "key": "step-review-2",
        "label": "Review 2",
        "status": "active",
    }


@pytest.mark.parametrize(
    "scenario_id",
    (
        "happy_path_accept",
        "retry_same_phase",
        "close_path",
        "invalid_outcome_mapping",
    ),
)
def test_cli_phase_cycle_visual_matches_shared_scenario_matrix(scenario_id: str):
    protocol = compile_protocol_definition(scenario_protocol_definition(scenario_id))
    phase = protocol.phases[0]
    state = PhaseCycleState.start(
        thread_id="thread-1",
        phase_name=phase.name,
        procedure_steps=phase_cycle_procedure_actions(phase),
    ).model_copy(update={"last_gate_outcome": get_scenario(scenario_id).outcome})

    expected_visual = _expected_phase_cycle_visual_payload(scenario_id)
    cli_visual = _phase_cycle_visual_payload(protocol=protocol, phase=phase, state=state)
    api_visual = _build_phase_cycle_visual(protocol=protocol, phase=phase, state=state)

    assert cli_visual == expected_visual
    assert api_visual == expected_visual


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
