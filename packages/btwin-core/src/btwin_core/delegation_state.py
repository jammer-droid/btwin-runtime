"""Delegation state persisted per thread."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

DelegationStatus = Literal[
    "idle",
    "running",
    "waiting_for_human",
    "blocked",
    "completed",
    "failed",
]


class DelegationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    status: DelegationStatus
    loop_iteration: int = 0
    current_phase: str | None = None
    current_cycle_index: int = 0
    target_role: str | None = None
    resolved_agent: str | None = None
    required_action: str | None = None
    expected_output: str | None = None
