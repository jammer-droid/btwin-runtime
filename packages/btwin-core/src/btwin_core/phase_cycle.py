"""Minimal phase-cycle state models for repeated protocol execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


PhaseCycleStatus = Literal["active", "completed", "blocked"]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PhaseCycleState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    phase_name: str
    cycle_index: int = 1
    current_step_index: int = 0
    procedure_steps: list[str] = Field(default_factory=list)
    current_step_label: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    status: PhaseCycleStatus = "active"
    last_completed_at: str | None = None
    last_gate_outcome: str | None = None
    last_cycle_outcome: str | None = None

    @classmethod
    def start(
        cls,
        *,
        thread_id: str,
        phase_name: str,
        procedure_steps: list[str] | None = None,
        last_cycle_outcome: str | None = None,
    ) -> "PhaseCycleState":
        return cls(
            thread_id=thread_id,
            phase_name=phase_name,
            cycle_index=1,
            current_step_index=0,
            procedure_steps=list(procedure_steps or []),
            current_step_label=(procedure_steps or [None])[0],
            completed_steps=[],
            status="active",
            last_completed_at=None,
            last_gate_outcome=None,
            last_cycle_outcome=last_cycle_outcome,
        )

    def finish_cycle(self, *, gate_outcome: str, next_phase: str | None) -> "PhaseCycleState":
        if next_phase == self.phase_name:
            return self.model_copy(
                update={
                    "cycle_index": self.cycle_index + 1,
                    "current_step_index": 0,
                    "current_step_label": self.procedure_steps[0] if self.procedure_steps else None,
                    "completed_steps": [],
                    "status": "active",
                    "last_completed_at": _iso_now(),
                    "last_gate_outcome": gate_outcome,
                    "last_cycle_outcome": gate_outcome,
                }
            )
        return self.model_copy(
            update={
                "current_step_label": None,
                "completed_steps": list(self.procedure_steps),
                "status": "completed",
                "last_completed_at": _iso_now(),
                "last_gate_outcome": gate_outcome,
                "last_cycle_outcome": gate_outcome,
            }
        )

    def record_local_recovery_block(self) -> "PhaseCycleState":
        return self.model_copy(
            update={
                "status": "blocked",
            }
        )
