"""Deterministic helpers for phase-cycle progression."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from btwin_core.context_core import ContextCore
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.protocol_store import Protocol, ProtocolPhase


class PhaseCycleAdvanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_state: PhaseCycleState
    context_core: ContextCore


def advance_phase_cycle(
    *,
    thread: dict[str, object],
    protocol: Protocol,
    current_state: PhaseCycleState,
    outcome: str,
) -> PhaseCycleAdvanceResult:
    next_phase_name = _resolve_next_phase(protocol, current_state.phase_name, outcome)
    if next_phase_name is None:
        next_state = current_state.finish_cycle(gate_outcome=outcome, next_phase=None)
        target_phase = _get_phase(protocol, current_state.phase_name)
    elif next_phase_name == current_state.phase_name:
        next_state = current_state.finish_cycle(gate_outcome=outcome, next_phase=next_phase_name)
        target_phase = _get_phase(protocol, next_phase_name)
    else:
        target_phase = _get_phase(protocol, next_phase_name)
        next_state = PhaseCycleState.start(
            thread_id=current_state.thread_id,
            phase_name=next_phase_name,
            procedure_steps=_procedure_actions(target_phase),
        )

    context_core = ContextCore(
        thread_goal=str(thread.get("topic") or thread.get("thread_id") or ""),
        phase_purpose=target_phase.description or target_phase.name,
        non_goals=[],
        required_result=_required_result(target_phase),
        last_cycle_outcome=outcome,
        next_expected_action=_next_expected_action(target_phase),
        current_cycle_index=next_state.cycle_index,
        current_step_label=next_state.current_step_label,
    )
    return PhaseCycleAdvanceResult(next_state=next_state, context_core=context_core)


def _resolve_next_phase(protocol: Protocol, current_phase_name: str, outcome: str) -> str | None:
    for transition in protocol.transitions:
        if transition.from_phase == current_phase_name and transition.on == outcome:
            return transition.to
    return None


def _get_phase(protocol: Protocol, phase_name: str) -> ProtocolPhase:
    for phase in protocol.phases:
        if phase.name == phase_name:
            return phase
    raise ValueError(f"Unknown phase: {phase_name}")


def _procedure_actions(phase: ProtocolPhase) -> list[str]:
    if not phase.procedure:
        return []
    return [step.action for step in phase.procedure]


def _next_expected_action(phase: ProtocolPhase) -> str | None:
    if phase.procedure:
        first_step = phase.procedure[0]
        return first_step.guidance or first_step.action
    return None


def _required_result(phase: ProtocolPhase) -> str:
    if phase.template:
        required_sections = [section.section for section in phase.template if section.required]
        if required_sections:
            return ", ".join(required_sections)
    return f"{phase.name} result"
