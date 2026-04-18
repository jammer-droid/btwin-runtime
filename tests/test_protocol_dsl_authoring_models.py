import pytest

from btwin_core.protocol_store import Protocol


def test_protocol_accepts_authoring_only_gate_and_outcome_policy_objects():
    proto = Protocol.model_validate(
        {
            "name": "review-loop",
            "phases": [
                {
                    "name": "review",
                    "actions": ["contribute"],
                    "gate": "review-gate",
                    "outcome_policy": "review-outcomes",
                },
                {"name": "decision", "actions": ["decide"]},
            ],
            "gates": [
                {
                    "name": "review-gate",
                    "description": "Authoring-only gate declaration.",
                    "routes": [
                        {
                            "outcome": "retry",
                            "target_phase": "review",
                            "alias": "Retry Loop",
                            "key": "retry-loop",
                        },
                        {
                            "outcome": "accept",
                            "target_phase": "decision",
                            "alias": "Accept Gate",
                            "key": "accept-gate",
                        },
                    ],
                }
            ],
            "outcome_policies": [
                {
                    "name": "review-outcomes",
                    "description": "Authoring-only outcome policy.",
                    "emitters": ["reviewer", "user"],
                    "actions": ["decide"],
                    "outcomes": ["retry", "accept"],
                }
            ],
            "transitions": [
                {"from": "review", "to": "review", "on": "retry"},
                {"from": "review", "to": "decision", "on": "accept"},
            ],
            "outcomes": ["retry", "accept"],
        }
    )

    assert proto.gates[0].name == "review-gate"
    assert proto.gates[0].authoring_only is True
    assert proto.gates[0].routes[0].outcome == "retry"
    assert proto.gates[0].routes[0].target_phase == "review"
    assert proto.outcome_policies[0].name == "review-outcomes"
    assert proto.outcome_policies[0].authoring_only is True
    assert proto.outcome_policies[0].emitters == ["reviewer", "user"]
    assert proto.phases[0].gate == "review-gate"
    assert proto.phases[0].outcome_policy == "review-outcomes"


def test_protocol_rejects_unknown_authoring_gate_reference():
    with pytest.raises(ValueError, match="unknown authoring gate"):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [{"name": "review", "gate": "missing-gate"}],
                "gates": [{"name": "review-gate", "routes": []}],
            }
        )


def test_protocol_rejects_unknown_outcome_policy_reference():
    with pytest.raises(ValueError, match="unknown outcome_policy"):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [{"name": "review", "outcome_policy": "missing-policy"}],
                "outcome_policies": [{"name": "review-outcomes"}],
            }
        )


def test_protocol_rejects_authoring_gate_route_with_unknown_target_phase():
    with pytest.raises(ValueError, match="unknown target_phase 'missing-phase'"):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [{"name": "review", "gate": "review-gate"}],
                "gates": [
                    {
                        "name": "review-gate",
                        "routes": [{"outcome": "retry", "target_phase": "missing-phase"}],
                    }
                ],
                "transitions": [{"from": "review", "to": "review", "on": "retry"}],
                "outcomes": ["retry"],
            }
        )


def test_protocol_rejects_duplicate_authoring_gate_routes_for_same_outcome():
    with pytest.raises(
        ValueError,
        match="gate 'review-gate' defines duplicate routes for outcome 'retry'",
    ):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [
                    {"name": "review", "gate": "review-gate"},
                    {"name": "decision-a"},
                    {"name": "decision-b"},
                ],
                "gates": [
                    {
                        "name": "review-gate",
                        "routes": [
                            {"outcome": "retry", "target_phase": "decision-a"},
                            {"outcome": "retry", "target_phase": "decision-b"},
                        ],
                    }
                ],
                "transitions": [{"from": "review", "to": "decision-a", "on": "retry"}],
                "outcomes": ["retry"],
            }
        )


def test_protocol_rejects_authoring_gate_route_with_outcome_outside_top_level_outcomes():
    with pytest.raises(ValueError, match="gate 'review-gate' uses undeclared outcome 'accept'"):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [{"name": "review", "gate": "review-gate"}],
                "gates": [
                    {
                        "name": "review-gate",
                        "routes": [{"outcome": "accept", "target_phase": "review"}],
                    }
                ],
                "transitions": [{"from": "review", "to": "review", "on": "retry"}],
                "outcomes": ["retry"],
            }
        )


def test_protocol_rejects_outcome_policy_outcome_outside_top_level_outcomes():
    with pytest.raises(
        ValueError, match="outcome_policy 'review-outcomes' uses undeclared outcome 'accept'"
    ):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [{"name": "review", "outcome_policy": "review-outcomes"}],
                "outcome_policies": [
                    {"name": "review-outcomes", "outcomes": ["accept"]}
                ],
                "outcomes": ["retry"],
            }
        )


def test_protocol_rejects_authoring_gate_route_that_conflicts_with_transition():
    with pytest.raises(
        ValueError,
        match="gate 'review-gate' route for phase 'review' and outcome 'retry' contradicts canonical transition target 'review'",
    ):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [
                    {"name": "review", "gate": "review-gate"},
                    {"name": "decision"},
                ],
                "gates": [
                    {
                        "name": "review-gate",
                        "routes": [{"outcome": "retry", "target_phase": "decision"}],
                    }
                ],
                "transitions": [{"from": "review", "to": "review", "on": "retry"}],
                "outcomes": ["retry"],
            }
        )


def test_protocol_rejects_authoring_gate_route_with_ambiguous_canonical_transition():
    with pytest.raises(
        ValueError,
        match="gate 'review-gate' route for phase 'review' and outcome 'retry' has ambiguous canonical transitions",
    ):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [
                    {"name": "review", "gate": "review-gate"},
                    {"name": "decision-a"},
                    {"name": "decision-b"},
                ],
                "gates": [
                    {
                        "name": "review-gate",
                        "routes": [{"outcome": "retry", "target_phase": "decision-a"}],
                    }
                ],
                "transitions": [
                    {"from": "review", "to": "decision-a", "on": "retry"},
                    {"from": "review", "to": "decision-b", "on": "retry"},
                ],
                "outcomes": ["retry"],
            }
        )


def test_protocol_rejects_authoring_gate_route_with_duplicate_same_target_transition():
    with pytest.raises(
        ValueError,
        match="gate 'review-gate' route for phase 'review' and outcome 'retry' has ambiguous canonical transitions",
    ):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [
                    {"name": "review", "gate": "review-gate"},
                    {"name": "decision"},
                ],
                "gates": [
                    {
                        "name": "review-gate",
                        "routes": [{"outcome": "retry", "target_phase": "decision"}],
                    }
                ],
                "transitions": [
                    {"from": "review", "to": "decision", "on": "retry"},
                    {"from": "review", "to": "decision", "on": "retry"},
                ],
                "outcomes": ["retry"],
            }
        )


def test_protocol_rejects_authoring_gate_route_without_canonical_transition():
    with pytest.raises(
        ValueError,
        match="gate 'review-gate' route for phase 'review' and outcome 'retry' has no canonical transition",
    ):
        Protocol.model_validate(
            {
                "name": "review-loop",
                "phases": [
                    {"name": "review", "gate": "review-gate"},
                    {"name": "decision"},
                ],
                "gates": [
                    {
                        "name": "review-gate",
                        "routes": [{"outcome": "retry", "target_phase": "decision"}],
                    }
                ],
                "transitions": [{"from": "review", "to": "decision", "on": "accept"}],
                "outcomes": ["retry", "accept"],
            }
        )


def test_protocol_keeps_existing_transition_only_yaml_compatible():
    proto = Protocol.model_validate(
        {
            "name": "review-loop",
            "phases": [{"name": "review", "actions": ["contribute"]}],
            "transitions": [{"from": "review", "to": "review", "on": "retry"}],
            "outcomes": ["retry", "accept"],
        }
    )

    assert proto.gates == []
    assert proto.outcome_policies == []
    assert proto.phases[0].gate is None
    assert proto.phases[0].outcome_policy is None
