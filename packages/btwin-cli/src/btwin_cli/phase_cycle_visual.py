"""Shared phase-cycle visual payload builder for CLI and API surfaces."""

from __future__ import annotations

from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_engine import resolve_phase_cycle_current_step_index
from btwin_core.protocol_store import Protocol, ProtocolPhase


def build_phase_cycle_visual_payload(
    *,
    protocol: Protocol | None,
    phase: ProtocolPhase | None,
    state: PhaseCycleState,
) -> dict[str, object]:
    procedure_nodes: list[dict[str, object]] = []
    current_step_index = resolve_phase_cycle_current_step_index(phase, state)
    raw_steps = phase.procedure if phase is not None and phase.procedure else None
    if raw_steps:
        for index, step in enumerate(raw_steps):
            status = "pending"
            if state.status == "completed":
                status = "completed"
            elif current_step_index is not None and index < current_step_index:
                status = "completed"
            elif current_step_index is not None and index == current_step_index:
                status = "active"
            procedure_nodes.append(
                {
                    "key": step.visual_key(),
                    "label": step.visual_label(),
                    "status": status,
                }
            )
    else:
        step_labels = [step for step in state.procedure_steps if isinstance(step, str)]
        for index, step in enumerate(step_labels):
            status = "pending"
            if state.status == "completed":
                status = "completed"
            elif current_step_index is not None and index < current_step_index:
                status = "completed"
            elif current_step_index is not None and index == current_step_index:
                status = "active"
            procedure_nodes.append({"key": step, "label": step, "status": status})
    gate_status = "completed" if state.status == "completed" else "pending"
    procedure_nodes.append({"key": "gate", "label": "Gate", "status": gate_status})

    gate_nodes: list[dict[str, object]] = []
    if protocol is not None:
        for transition in protocol.transitions:
            if transition.from_phase != state.phase_name:
                continue
            gate_nodes.append(
                {
                    "key": transition.visual_key(),
                    "label": transition.visual_label(),
                    "status": "completed" if transition.on and state.last_gate_outcome == transition.on else "pending",
                    "target_phase": transition.to,
                }
            )
    elif state.last_gate_outcome:
        gate_nodes.append(
            {
                "key": state.last_gate_outcome,
                "label": state.last_gate_outcome,
                "status": "completed",
                "target_phase": state.phase_name,
            }
        )

    guard_nodes: list[dict[str, object]] = []
    if protocol is not None and phase is not None:
        declared_guard_set = protocol.get_guard_set(phase.guard_set)
        if declared_guard_set is not None:
            guard_nodes = [
                {"key": guard, "label": guard, "status": "declared"}
                for guard in declared_guard_set.guards
            ]

    return {"procedure": procedure_nodes, "gates": gate_nodes, "guards": guard_nodes}
