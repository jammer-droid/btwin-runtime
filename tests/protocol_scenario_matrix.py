from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

PreviewStatus = Literal["valid", "note", "invalid"]


@dataclass(frozen=True)
class ScenarioTraceCheckpoint:
    label: str
    phase: str
    procedure_key: str | None
    gate_key: str | None
    outcome: str | None
    target_phase: str | None
    cycle_index: int
    next_cycle_index: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "phase": self.phase,
            "procedure_key": self.procedure_key,
            "gate_key": self.gate_key,
            "outcome": self.outcome,
            "target_phase": self.target_phase,
            "cycle_index": self.cycle_index,
            "next_cycle_index": self.next_cycle_index,
        }


@dataclass(frozen=True)
class ScenarioVisualExpectation:
    key: str
    label: str
    status: str
    target_phase: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "key": self.key,
            "label": self.label,
            "status": self.status,
        }
        if self.target_phase is not None:
            payload["target_phase"] = self.target_phase
        return payload


@dataclass(frozen=True)
class ScenarioFixture:
    scenario_id: str
    protocol_name: str
    protocol_definition: dict[str, object]
    preview_status: PreviewStatus
    simulation_trace: tuple[ScenarioTraceCheckpoint, ...]
    live_smoke_required: bool
    gate_key: str | None
    procedure_key: str | None
    outcome: str | None
    target_phase: str | None
    cycle_index_changes: tuple[tuple[int, int], ...]
    visual_procedure: tuple[ScenarioVisualExpectation, ...] = ()
    visual_gates: tuple[ScenarioVisualExpectation, ...] = ()


def _review_loop_protocol_definition() -> dict[str, object]:
    return {
        "name": "review-loop",
        "description": "Review loop with retry, accept, and close outcomes.",
        "outcomes": ["retry", "accept", "close"],
        "phases": [
            {
                "name": "review",
                "description": "Review and revise the work.",
                "actions": ["contribute"],
                "template": [{"section": "completed", "required": True}],
                "procedure": [
                    {"key": "review-pass", "role": "reviewer", "action": "review", "alias": "Review"},
                    {"key": "revise-pass", "role": "implementer", "action": "revise", "alias": "Revise"},
                ],
            },
            {
                "name": "decision",
                "description": "Record the final decision.",
                "actions": ["decide"],
            },
        ],
        "transitions": [
            {"key": "retry-loop", "from": "review", "to": "review", "on": "retry", "alias": "Retry Gate"},
            {"key": "accept-gate", "from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate"},
            {"key": "close-gate", "from": "review", "to": "decision", "on": "close", "alias": "Close Gate"},
        ],
    }


def _fixture(
    *,
    scenario_id: str,
    preview_status: PreviewStatus,
    simulation_trace: tuple[ScenarioTraceCheckpoint, ...],
    live_smoke_required: bool,
    gate_key: str | None,
    procedure_key: str | None,
    outcome: str | None,
    target_phase: str | None,
    cycle_index_changes: tuple[tuple[int, int], ...],
    visual_procedure: tuple[ScenarioVisualExpectation, ...] = (),
    visual_gates: tuple[ScenarioVisualExpectation, ...] = (),
) -> ScenarioFixture:
    return ScenarioFixture(
        scenario_id=scenario_id,
        protocol_name="review-loop",
        protocol_definition=_review_loop_protocol_definition(),
        preview_status=preview_status,
        simulation_trace=simulation_trace,
        live_smoke_required=live_smoke_required,
        gate_key=gate_key,
        procedure_key=procedure_key,
        outcome=outcome,
        target_phase=target_phase,
        cycle_index_changes=cycle_index_changes,
        visual_procedure=visual_procedure,
        visual_gates=visual_gates,
    )


_SCENARIOS: dict[str, ScenarioFixture] = {
    "happy_path_accept": _fixture(
        scenario_id="happy_path_accept",
        preview_status="valid",
        simulation_trace=(
            ScenarioTraceCheckpoint(
                label="accept",
                phase="review",
                procedure_key="review-pass",
                gate_key="accept-gate",
                outcome="accept",
                target_phase="decision",
                cycle_index=1,
                next_cycle_index=1,
            ),
        ),
        live_smoke_required=True,
        gate_key="accept-gate",
        procedure_key="review-pass",
        outcome="accept",
        target_phase="decision",
        cycle_index_changes=((1, 1),),
        visual_procedure=(
            ScenarioVisualExpectation(key="review-pass", label="Review", status="active"),
            ScenarioVisualExpectation(key="revise-pass", label="Revise", status="pending"),
        ),
        visual_gates=(
            ScenarioVisualExpectation(
                key="retry-loop",
                label="Retry Gate",
                status="pending",
                target_phase="review",
            ),
            ScenarioVisualExpectation(
                key="accept-gate",
                label="Accept Gate",
                status="completed",
                target_phase="decision",
            ),
            ScenarioVisualExpectation(
                key="close-gate",
                label="Close Gate",
                status="pending",
                target_phase="decision",
            ),
        ),
    ),
    "retry_same_phase": _fixture(
        scenario_id="retry_same_phase",
        preview_status="valid",
        simulation_trace=(
            ScenarioTraceCheckpoint(
                label="retry-cycle-1",
                phase="review",
                procedure_key="review-pass",
                gate_key="retry-loop",
                outcome="retry",
                target_phase="review",
                cycle_index=1,
                next_cycle_index=2,
            ),
            ScenarioTraceCheckpoint(
                label="retry-cycle-2",
                phase="review",
                procedure_key="review-pass",
                gate_key="retry-loop",
                outcome="retry",
                target_phase="review",
                cycle_index=2,
                next_cycle_index=3,
            ),
        ),
        live_smoke_required=True,
        gate_key="retry-loop",
        procedure_key="review-pass",
        outcome="retry",
        target_phase="review",
        cycle_index_changes=((1, 2), (2, 3)),
        visual_procedure=(
            ScenarioVisualExpectation(key="review-pass", label="Review", status="active"),
            ScenarioVisualExpectation(key="revise-pass", label="Revise", status="pending"),
        ),
        visual_gates=(
            ScenarioVisualExpectation(
                key="retry-loop",
                label="Retry Gate",
                status="completed",
                target_phase="review",
            ),
            ScenarioVisualExpectation(
                key="accept-gate",
                label="Accept Gate",
                status="pending",
                target_phase="decision",
            ),
            ScenarioVisualExpectation(
                key="close-gate",
                label="Close Gate",
                status="pending",
                target_phase="decision",
            ),
        ),
    ),
    "blocked_stop_missing_contribution": _fixture(
        scenario_id="blocked_stop_missing_contribution",
        preview_status="note",
        simulation_trace=(
            ScenarioTraceCheckpoint(
                label="stop-block",
                phase="review",
                procedure_key="review-pass",
                gate_key="retry-loop",
                outcome="stop",
                target_phase="review",
                cycle_index=1,
                next_cycle_index=1,
            ),
        ),
        live_smoke_required=True,
        gate_key="retry-loop",
        procedure_key="review-pass",
        outcome="stop",
        target_phase="review",
        cycle_index_changes=((1, 1),),
    ),
    "invalid_outcome_mapping": _fixture(
        scenario_id="invalid_outcome_mapping",
        preview_status="invalid",
        simulation_trace=(
            ScenarioTraceCheckpoint(
                label="preview-fail",
                phase="review",
                procedure_key="review-pass",
                gate_key="accept-gate",
                outcome="reject",
                target_phase=None,
                cycle_index=1,
                next_cycle_index=None,
            ),
        ),
        live_smoke_required=False,
        gate_key="accept-gate",
        procedure_key="review-pass",
        outcome="reject",
        target_phase=None,
        cycle_index_changes=(),
        visual_procedure=(
            ScenarioVisualExpectation(key="review-pass", label="Review", status="active"),
            ScenarioVisualExpectation(key="revise-pass", label="Revise", status="pending"),
        ),
        visual_gates=(
            ScenarioVisualExpectation(
                key="retry-loop",
                label="Retry Gate",
                status="pending",
                target_phase="review",
            ),
            ScenarioVisualExpectation(
                key="accept-gate",
                label="Accept Gate",
                status="pending",
                target_phase="decision",
            ),
            ScenarioVisualExpectation(
                key="close-gate",
                label="Close Gate",
                status="pending",
                target_phase="decision",
            ),
        ),
    ),
    "close_path": _fixture(
        scenario_id="close_path",
        preview_status="valid",
        simulation_trace=(
            ScenarioTraceCheckpoint(
                label="close",
                phase="review",
                procedure_key="review-pass",
                gate_key="close-gate",
                outcome="close",
                target_phase="decision",
                cycle_index=1,
                next_cycle_index=1,
            ),
        ),
        live_smoke_required=True,
        gate_key="close-gate",
        procedure_key="review-pass",
        outcome="close",
        target_phase="decision",
        cycle_index_changes=((1, 1),),
        visual_procedure=(
            ScenarioVisualExpectation(key="review-pass", label="Review", status="active"),
            ScenarioVisualExpectation(key="revise-pass", label="Revise", status="pending"),
        ),
        visual_gates=(
            ScenarioVisualExpectation(
                key="retry-loop",
                label="Retry Gate",
                status="pending",
                target_phase="review",
            ),
            ScenarioVisualExpectation(
                key="accept-gate",
                label="Accept Gate",
                status="pending",
                target_phase="decision",
            ),
            ScenarioVisualExpectation(
                key="close-gate",
                label="Close Gate",
                status="completed",
                target_phase="decision",
            ),
        ),
    ),
    "attach_seed_first_cycle": _fixture(
        scenario_id="attach_seed_first_cycle",
        preview_status="valid",
        simulation_trace=(
            ScenarioTraceCheckpoint(
                label="seed",
                phase="review",
                procedure_key="review-pass",
                gate_key="retry-loop",
                outcome=None,
                target_phase="review",
                cycle_index=1,
                next_cycle_index=1,
            ),
        ),
        live_smoke_required=True,
        gate_key="retry-loop",
        procedure_key="review-pass",
        outcome=None,
        target_phase="review",
        cycle_index_changes=((1, 1),),
    ),
}

SCENARIO_IDS: tuple[str, ...] = tuple(_SCENARIOS.keys())


def list_scenarios() -> tuple[ScenarioFixture, ...]:
    return tuple(_SCENARIOS[scenario_id] for scenario_id in SCENARIO_IDS)


def get_scenario(scenario_id: str) -> ScenarioFixture:
    try:
        return _SCENARIOS[scenario_id]
    except KeyError as exc:
        available = ", ".join(SCENARIO_IDS)
        raise KeyError(f"Unknown scenario_id '{scenario_id}'. Available IDs: {available}") from exc


def scenario_protocol_definition(scenario_id: str) -> dict[str, object]:
    return deepcopy(get_scenario(scenario_id).protocol_definition)
