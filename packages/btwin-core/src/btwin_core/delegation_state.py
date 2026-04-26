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
    updated_at: str | None = None
    loop_iteration: int = 0
    current_phase: str | None = None
    current_cycle_index: int = 0
    target_role: str | None = None
    resolved_agent: str | None = None
    required_action: str | None = None
    expected_output: str | None = None
    fulfillment_mode: str = "registered_agent"
    parent_executor: str | None = None
    subagent_profile: str | None = None
    subagent_type: str | None = None
    executor_id: str | None = None
    spawn_packet: dict[str, object] | None = None
    reason_blocked: str | None = None
    last_dispatch_message_id: str | None = None
    last_result_message_id: str | None = None
    last_resume_token: str | None = None
    stop_reason: str | None = None


_COMPLETED_ACTIVE_ASSIGNMENT_FIELDS = {
    "current_phase",
    "current_cycle_index",
    "target_role",
    "resolved_agent",
    "required_action",
    "expected_output",
    "fulfillment_mode",
    "parent_executor",
    "subagent_profile",
    "subagent_type",
    "executor_id",
    "spawn_packet",
    "reason_blocked",
    "last_resume_token",
}


def delegation_status_payload(state: DelegationState) -> dict[str, object]:
    payload = state.model_dump(exclude_none=True)
    if state.status == "completed":
        for field_name in _COMPLETED_ACTIVE_ASSIGNMENT_FIELDS:
            payload.pop(field_name, None)
    return payload
