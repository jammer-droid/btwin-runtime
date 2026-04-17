"""Minimal re-anchor context state for phase-driven loop supervision."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ContextCore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_goal: str
    phase_purpose: str
    non_goals: list[str] = Field(default_factory=list)
    required_result: str
    last_cycle_outcome: str | None = None
    next_expected_role: str | None = None
    next_expected_action: str | None = None
    current_cycle_index: int | None = None
    current_step_label: str | None = None
    current_step_alias: str | None = None
    current_step_role: str | None = None
