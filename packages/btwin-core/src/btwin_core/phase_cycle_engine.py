"""Deterministic helpers for phase-cycle progression."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from btwin_core.context_core import ContextCore
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolProcedureStep


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
            procedure_steps=phase_cycle_procedure_actions(target_phase),
            last_cycle_outcome=outcome,
        )

    context_core = build_phase_cycle_context_core(
        thread=thread,
        phase=target_phase,
        state=next_state,
        last_cycle_outcome=outcome,
    )
    return PhaseCycleAdvanceResult(next_state=next_state, context_core=context_core)


def build_phase_cycle_context_core(
    *,
    thread: dict[str, object],
    phase: ProtocolPhase,
    state: PhaseCycleState,
    last_cycle_outcome: str | None = None,
) -> ContextCore:
    current_step = _current_step(phase, state)
    return ContextCore(
        thread_goal=str(thread.get("topic") or thread.get("thread_id") or ""),
        phase_purpose=phase.description or phase.name,
        non_goals=[],
        required_result=_required_result(phase),
        last_cycle_outcome=(
            last_cycle_outcome
            if last_cycle_outcome is not None
            else state.last_cycle_outcome or state.last_gate_outcome
        ),
        next_expected_role=current_step.role if current_step is not None else None,
        next_expected_action=_step_guidance(current_step),
        current_cycle_index=state.cycle_index,
        current_step_label=state.current_step_label,
        current_step_alias=_step_alias(current_step, state.current_step_label),
        current_step_role=current_step.role if current_step is not None else None,
    )


def resolve_phase_cycle_current_step_index(
    phase: ProtocolPhase | None,
    state: PhaseCycleState,
) -> int | None:
    if phase is not None and phase.procedure:
        if 0 <= state.current_step_index < len(phase.procedure):
            indexed_step = phase.procedure[state.current_step_index]
            if state.current_step_label is None or indexed_step.action == state.current_step_label:
                return state.current_step_index
        if state.current_step_label is not None:
            for index, step in enumerate(phase.procedure):
                if step.action == state.current_step_label:
                    return index
        if state.status == "active":
            return 0
        return None
    return _resolve_step_index_from_labels(state.procedure_steps, state)


def phase_cycle_procedure_actions(phase: ProtocolPhase) -> list[str]:
    if not phase.procedure:
        return []
    return [step.action for step in phase.procedure]


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


def _current_step(phase: ProtocolPhase, state: PhaseCycleState) -> ProtocolProcedureStep | None:
    if not phase.procedure:
        return None
    current_index = resolve_phase_cycle_current_step_index(phase, state)
    if current_index is None:
        return None
    return phase.procedure[current_index]


def _step_guidance(step: ProtocolProcedureStep | None) -> str | None:
    if step is None:
        return None
    return step.guidance or step.action


def _step_alias(step: ProtocolProcedureStep | None, fallback_label: str | None) -> str | None:
    if step is None:
        return fallback_label
    return step.alias or step.action or fallback_label


def _required_result(phase: ProtocolPhase) -> str:
    if phase.template:
        required_sections = [section.section for section in phase.template if section.required]
        if required_sections:
            return ", ".join(required_sections)
    return f"{phase.name} result"


def _resolve_step_index_from_labels(
    step_labels: list[str] | None,
    state: PhaseCycleState,
) -> int | None:
    labels = list(step_labels or [])
    if 0 <= state.current_step_index < len(labels):
        indexed_label = labels[state.current_step_index]
        if state.current_step_label is None or indexed_label == state.current_step_label:
            return state.current_step_index
    if state.current_step_label is not None:
        for index, label in enumerate(labels):
            if label == state.current_step_label:
                return index
    if state.status == "active" and labels:
        return 0
    return None
