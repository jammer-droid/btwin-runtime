"""Protocol flow planning helpers for next-action decisions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from btwin_core.protocol_store import Protocol
from btwin_core.protocol_validator import ProtocolValidator

ProtocolSuggestedAction = Literal["submit_contribution", "advance_phase", "record_outcome", "close_thread"]


class ProtocolNextPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    protocol: str
    current_phase: str | None
    passed: bool
    missing: list[dict[str, object]] = Field(default_factory=list)
    valid_outcomes: list[str] = Field(default_factory=list)
    requested_outcome: str | None = None
    next_phase: str | None = None
    suggested_action: ProtocolSuggestedAction
    error: str | None = None
    manual_outcome_required: bool = False


def describe_next(
    thread: dict,
    protocol: Protocol,
    contributions: list[dict],
    *,
    outcome: str | None = None,
) -> ProtocolNextPlan:
    """Describe the next valid protocol action for a thread."""
    thread_id = str(thread.get("thread_id") or "")
    current_phase = thread.get("current_phase")

    if not thread_id:
        raise ValueError("thread must include a thread_id")

    if not isinstance(current_phase, str) or not current_phase:
        return ProtocolNextPlan(
            thread_id=thread_id,
            protocol=protocol.name,
            current_phase=current_phase if isinstance(current_phase, str) else None,
            passed=False,
            suggested_action="record_outcome",
            error="phase_not_found",
            requested_outcome=outcome,
        )

    phase = next((item for item in protocol.phases if item.name == current_phase), None)
    if phase is None:
        return ProtocolNextPlan(
            thread_id=thread_id,
            protocol=protocol.name,
            current_phase=current_phase,
            passed=False,
            suggested_action="record_outcome",
            error="phase_not_found",
            requested_outcome=outcome,
        )

    phase_participants = thread.get("phase_participants", [])
    if not isinstance(phase_participants, list):
        phase_participants = []

    validation = ProtocolValidator.validate_phase(
        phase_participants=[str(name) for name in phase_participants if isinstance(name, str)],
        template_sections=phase.template or [],
        contributions=contributions,
    )

    phase_index = next((idx for idx, item in enumerate(protocol.phases) if item.name == current_phase), -1)
    sequential_next = protocol.phases[phase_index + 1].name if 0 <= phase_index < len(protocol.phases) - 1 else None
    branch_transitions = [t for t in protocol.transitions if t.from_phase == current_phase and t.on]
    default_transition = next((t for t in protocol.transitions if t.from_phase == current_phase and t.on is None), None)
    valid_outcomes = list(protocol.outcomes) or [transition.on for transition in branch_transitions if transition.on]

    next_phase = None
    suggested_action: ProtocolSuggestedAction = "close_thread"
    manual_outcome_required = False
    if not validation.passed:
        suggested_action = "submit_contribution"
    elif outcome:
        if not valid_outcomes or outcome not in valid_outcomes:
            return ProtocolNextPlan(
                thread_id=thread_id,
                protocol=protocol.name,
                current_phase=current_phase,
                passed=validation.passed,
                missing=validation.missing,
                valid_outcomes=[str(outcome_value) for outcome_value in valid_outcomes if outcome_value],
                requested_outcome=outcome,
                suggested_action="record_outcome",
                error="unsupported_outcome",
            )
        matched = next((t for t in branch_transitions if t.on == outcome), None)
        next_phase = matched.to if matched else None
        if next_phase:
            suggested_action = "advance_phase"
        else:
            suggested_action = "record_outcome"
            manual_outcome_required = True
    elif valid_outcomes:
        suggested_action = "record_outcome"
        manual_outcome_required = True
    else:
        next_phase = default_transition.to if default_transition else sequential_next
        if next_phase:
            suggested_action = "advance_phase"

    return ProtocolNextPlan(
        thread_id=thread_id,
        protocol=protocol.name,
        current_phase=current_phase,
        passed=validation.passed,
        missing=validation.missing,
        valid_outcomes=[str(outcome_value) for outcome_value in valid_outcomes if outcome_value],
        requested_outcome=outcome,
        next_phase=next_phase,
        suggested_action=suggested_action,
        manual_outcome_required=manual_outcome_required,
    )
