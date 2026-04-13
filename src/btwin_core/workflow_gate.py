"""Gate validators and transition logic for orchestration workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from btwin_core.orchestration_models import OrchestrationRecord, OrchestrationStatus

GateErrorCode = Literal[
    "INVALID_STATE_TRANSITION",
    "CONCURRENT_MODIFICATION",
    "FORBIDDEN",
]

_ALLOWED_TRANSITIONS: MappingProxyType[OrchestrationStatus, frozenset[OrchestrationStatus]] = MappingProxyType(
    {
        "draft": frozenset({"handed_off", "completed"}),
        "handed_off": frozenset({"completed"}),
        "completed": frozenset(),
    }
)


@dataclass(frozen=True)
class GateDecision:
    ok: bool
    error_code: GateErrorCode | None = None
    message: str = ""
    idempotent: bool = False
    status: OrchestrationStatus | None = None
    version: int | None = None
    details: dict[str, object] = field(default_factory=dict)


def validate_actor(actor_agent: str, allowed_agents: set[str]) -> GateDecision:
    if actor_agent in allowed_agents:
        return GateDecision(ok=True, status=None, version=None)

    return GateDecision(
        ok=False,
        error_code="FORBIDDEN",
        message="actor agent is not allowed",
        details={"actorAgent": actor_agent},
    )


def validate_promotion_approval(actor_agent: str) -> GateDecision:
    if actor_agent == "main":
        return GateDecision(ok=True)

    return GateDecision(
        ok=False,
        error_code="FORBIDDEN",
        message="only Vincent(main) can approve promotion",
        details={"actorAgent": actor_agent},
    )


def apply_transition(record: OrchestrationRecord, target_status: OrchestrationStatus, expected_version: int) -> GateDecision:
    """Apply orchestration status transition with idempotency and CAS checks."""
    if record.status == target_status:
        return GateDecision(
            ok=True,
            idempotent=True,
            status=record.status,
            version=record.version,
            message="idempotent retry",
            details={"currentVersion": record.version, "expectedVersion": expected_version},
        )

    if record.version != expected_version:
        return GateDecision(
            ok=False,
            error_code="CONCURRENT_MODIFICATION",
            message="expectedVersion does not match current version",
            details={"currentVersion": record.version, "expectedVersion": expected_version},
        )

    allowed_targets = _ALLOWED_TRANSITIONS.get(record.status, set())
    if target_status not in allowed_targets:
        return GateDecision(
            ok=False,
            error_code="INVALID_STATE_TRANSITION",
            message=f"cannot transition from {record.status} to {target_status}",
            details={"from": record.status, "to": target_status},
        )

    return GateDecision(ok=True, status=target_status, version=record.version + 1)


_WORKFLOW_TRANSITIONS: MappingProxyType[str, frozenset[str]] = MappingProxyType({
    "active": frozenset({"completed", "escalated", "cancelled"}),
    "completed": frozenset(),
    "escalated": frozenset(),
    "cancelled": frozenset(),
})

_TASK_TRANSITIONS: MappingProxyType[str, frozenset[str]] = MappingProxyType({
    "pending": frozenset({"in_progress"}),
    "in_progress": frozenset({"done", "blocked", "escalated"}),
    "blocked": frozenset({"in_progress"}),
    "done": frozenset(),
    "escalated": frozenset(),
})

_RUN_TRANSITIONS: MappingProxyType[str, frozenset[str]] = MappingProxyType({
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"completed", "blocked", "interrupted", "cancelled"}),
    "blocked": frozenset({"running"}),
    "completed": frozenset(),
    "interrupted": frozenset(),
    "cancelled": frozenset(),
})

_PHASE_TRANSITIONS: MappingProxyType[str, frozenset[str]] = MappingProxyType({
    "implement": frozenset({"review"}),
    "review": frozenset({"fix"}),
    "fix": frozenset({"review"}),
})


def _validate_transition(current: str, target: str, table: MappingProxyType[str, frozenset[str]]) -> GateDecision:
    if current == target:
        return GateDecision(ok=True, idempotent=True, message="idempotent retry")
    allowed = table.get(current, frozenset())
    if target not in allowed:
        return GateDecision(
            ok=False,
            error_code="INVALID_STATE_TRANSITION",
            message=f"cannot transition from {current} to {target}",
            details={"from": current, "to": target},
        )
    return GateDecision(ok=True)


def validate_workflow_transition(current: str, target: str) -> GateDecision:
    return _validate_transition(current, target, _WORKFLOW_TRANSITIONS)


def validate_task_transition(current: str, target: str) -> GateDecision:
    return _validate_transition(current, target, _TASK_TRANSITIONS)


def validate_run_transition(current: str, target: str) -> GateDecision:
    return _validate_transition(current, target, _RUN_TRANSITIONS)


def validate_phase_transition(current: str, target: str) -> GateDecision:
    return _validate_transition(current, target, _PHASE_TRANSITIONS)
