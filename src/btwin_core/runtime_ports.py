"""Runtime integration ports (contracts only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol


ApprovalState = Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"]


@dataclass(slots=True)
class RecallQuery:
    query: str
    scope: str = "default"
    limit: int = 5


@dataclass(slots=True)
class RecallResult:
    record_id: str
    summary: str
    source: str
    confidence: float
    version: int
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryEntry:
    content: str
    doc_version: int


@dataclass(slots=True)
class MemoryRef:
    record_id: str
    doc_version: int


@dataclass(slots=True)
class Subject:
    subject_id: str
    roles: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AuthorizationDecision:
    allowed: bool
    policy_id: str
    decision_reason: str
    ttl: int


@dataclass(slots=True)
class ApprovalTicket:
    ticket_id: str
    status: ApprovalState = "PENDING"


@dataclass(slots=True)
class ApprovalStatus:
    ticket_id: str
    status: ApprovalState
    approver: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class AuditEvent:
    event_type: str
    actor: str
    trace_id: str
    doc_version: int
    checksum: str
    payload: dict[str, object] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class VerificationReport:
    ok: bool
    failed_ranges: list[str] = field(default_factory=list)


class RecallPort(Protocol):
    def recall(self, query: RecallQuery) -> list[RecallResult]: ...

    def remember(
        self,
        entry: MemoryEntry,
        tags: list[str] | None = None,
        source: str | None = None,
        timestamp: datetime | None = None,
    ) -> MemoryRef: ...


class IdentityPort(Protocol):
    def resolve_subject(self, subject_hint: str) -> Subject: ...

    def authorize(self, subject: Subject, action: str, resource: str) -> AuthorizationDecision: ...


class ApprovalPort(Protocol):
    def request_approval(self, action: str, risk_level: str, context: dict[str, object]) -> ApprovalTicket: ...

    def get_approval(self, ticket_id: str) -> ApprovalStatus: ...

    def record_approval_decision(self, ticket_id: str, approver: str, decision: ApprovalState, reason: str) -> None: ...


class AuditPort(Protocol):
    def append(self, event: AuditEvent) -> None: ...

    def query(
        self,
        *,
        trace_id: str | None = None,
        actor: str | None = None,
        event_type: str | None = None,
        time_range: tuple[datetime, datetime] | None = None,
        limit: int = 500,
    ) -> list[AuditEvent]: ...

    def verify_integrity(self, range_name: str) -> VerificationReport: ...
