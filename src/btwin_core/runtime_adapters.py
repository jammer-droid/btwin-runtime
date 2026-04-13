"""Runtime adapter implementations for attached/standalone modes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
import math
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from btwin_core.audit import AuditLogger
from btwin_core.runtime_ports import (
    AuditEvent,
    AuditPort,
    MemoryEntry,
    MemoryRef,
    RecallPort,
    RecallQuery,
    RecallResult,
    VerificationReport,
)


class OpenClawMemoryInterface(Protocol):
    def memory_search(self, *, query: str, scope: str, limit: int) -> list[dict[str, object]]: ...

    def memory_get(self, *, record_id: str) -> dict[str, object] | None: ...

    def memory_remember(
        self,
        *,
        content: str,
        tags: list[str],
        source: str,
        timestamp: datetime,
    ) -> dict[str, object]: ...



def _coerce_float(value: object, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default



def _coerce_int(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass(slots=True)
class StandaloneRecallAdapter(RecallPort):
    journal_path: Path

    def __post_init__(self) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)

    def recall(self, query: RecallQuery) -> list[RecallResult]:
        if not self.journal_path.exists():
            return []

        hits: list[RecallResult] = []
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue

            content = str(row.get("content", ""))
            if query.query.lower() not in content.lower():
                continue

            record_id = str(row.get("record_id", ""))
            if not record_id:
                continue
            hits.append(
                RecallResult(
                    record_id=record_id,
                    summary=content[:160],
                    source=str(row.get("source", "standalone")),
                    confidence=0.5,
                    version=_coerce_int(row.get("doc_version"), default=1),
                    metadata={"tags": row.get("tags", [])},
                )
            )
            if len(hits) >= query.limit:
                break
        return hits

    def remember(
        self,
        entry: MemoryEntry,
        tags: list[str] | None = None,
        source: str | None = None,
        timestamp: datetime | None = None,
    ) -> MemoryRef:
        record_id = f"mem_{uuid4().hex[:12]}"
        payload = {
            "record_id": record_id,
            "doc_version": entry.doc_version,
            "content": entry.content,
            "tags": tags or [],
            "source": source or "standalone",
            "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
        }
        with self.journal_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return MemoryRef(record_id=record_id, doc_version=entry.doc_version)


@dataclass(slots=True)
class OpenClawRecallAdapter(RecallPort):
    memory: OpenClawMemoryInterface

    def recall(self, query: RecallQuery) -> list[RecallResult]:
        rows = self.memory.memory_search(query=query.query, scope=query.scope, limit=query.limit)
        results: list[RecallResult] = []
        for row in rows:
            record_id = str(row.get("record_id") or row.get("id") or "")
            if not record_id:
                continue

            confidence = _coerce_float(row.get("confidence"), default=0.0)
            version = _coerce_int(row.get("version") or row.get("doc_version"), default=1)

            results.append(
                RecallResult(
                    record_id=record_id,
                    summary=str(row.get("summary") or row.get("content") or ""),
                    source=str(row.get("source") or "openclaw"),
                    confidence=confidence,
                    version=version,
                    metadata={"raw": row},
                )
            )
        return results

    def remember(
        self,
        entry: MemoryEntry,
        tags: list[str] | None = None,
        source: str | None = None,
        timestamp: datetime | None = None,
    ) -> MemoryRef:
        row = self.memory.memory_remember(
            content=entry.content,
            tags=tags or [],
            source=source or "btwin",
            timestamp=timestamp or datetime.now(UTC),
        )
        record_id = str(row.get("record_id") or row.get("id") or "")
        if not record_id:
            record_id = f"mem_{uuid4().hex[:12]}"
        version = _coerce_int(row.get("doc_version") or row.get("version"), default=entry.doc_version)
        return MemoryRef(record_id=record_id, doc_version=version)


@dataclass(slots=True)
class RuntimeAuditAdapter(AuditPort):
    logger: AuditLogger
    mode: str

    def append(self, event: AuditEvent) -> None:
        envelope = {
            "envelopeVersion": "1.0",
            "mode": self.mode,
            "actor": event.actor,
            "traceId": event.trace_id,
            "docVersion": event.doc_version,
            "checksum": event.checksum,
            "payload": event.payload,
            "timestamp": event.timestamp.isoformat(),
        }
        self.logger.log(
            event_type=event.event_type,
            payload=envelope,
            trace_id=event.trace_id,
        )

    def query(
        self,
        *,
        trace_id: str | None = None,
        actor: str | None = None,
        event_type: str | None = None,
        time_range: tuple[datetime, datetime] | None = None,
        limit: int = 500,
    ) -> list[AuditEvent]:
        rows = self.logger.tail(limit=limit)
        events: list[AuditEvent] = []
        for row in rows:
            if event_type and row.get("eventType") != event_type:
                continue
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if trace_id and payload.get("traceId") != trace_id:
                continue
            if actor and payload.get("actor") != actor:
                continue
            stamp = row.get("timestamp")
            dt = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if isinstance(stamp, str) else datetime.now(UTC)
            if time_range and not (time_range[0] <= dt <= time_range[1]):
                continue
            events.append(
                AuditEvent(
                    event_type=str(row.get("eventType")),
                    actor=str(payload.get("actor", "unknown")),
                    trace_id=str(payload.get("traceId", "")),
                    doc_version=int(payload.get("docVersion", 0)),
                    checksum=str(payload.get("checksum", "")),
                    payload=dict(payload.get("payload") or {}),
                    timestamp=dt,
                )
            )
        return events

    def verify_integrity(self, range_name: str) -> VerificationReport:
        log_path = self.logger.file_path
        if not log_path.exists():
            return VerificationReport(ok=True)

        required_fields = {"timestamp", "eventType", "traceId"}
        failed: list[str] = []
        seen_any = False

        with log_path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                seen_any = True
                # 1) Validate JSON parsing
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    failed.append(f"line {line_no}: invalid JSON")
                    continue

                if not isinstance(entry, dict):
                    failed.append(f"line {line_no}: entry is not a JSON object")
                    continue

                # 2) If range_name is provided, scope to events matching that event type
                if range_name:
                    event_type = entry.get("eventType")
                    if event_type != range_name:
                        continue

                # 3) Validate required fields
                missing = required_fields - entry.keys()
                if missing:
                    failed.append(f"line {line_no}: missing fields {sorted(missing)}")

        if not seen_any:
            return VerificationReport(ok=True)

        return VerificationReport(ok=len(failed) == 0, failed_ranges=failed)


@dataclass(slots=True)
class RuntimeAdapters:
    recall: RecallPort
    audit: RuntimeAuditAdapter
    recall_backend: str
    degraded: bool = False
    degraded_reason: str | None = None


def build_runtime_adapters(
    *,
    mode: str,
    data_dir: Path,
    audit_logger: AuditLogger,
    openclaw_memory: OpenClawMemoryInterface | None = None,
) -> RuntimeAdapters:
    degraded = False
    degraded_reason: str | None = None

    if mode == "attached" and openclaw_memory is not None:
        recall: RecallPort = OpenClawRecallAdapter(memory=openclaw_memory)
        recall_backend = "openclaw"
    elif mode == "attached":
        recall = StandaloneRecallAdapter(journal_path=data_dir / "memory_journal.jsonl")
        recall_backend = "standalone-journal"
        degraded = True
        degraded_reason = "attached mode requested but openclaw memory binding is unavailable"
    else:
        recall = StandaloneRecallAdapter(journal_path=data_dir / "memory_journal.jsonl")
        recall_backend = "standalone-journal"

    audit = RuntimeAuditAdapter(logger=audit_logger, mode=mode)
    return RuntimeAdapters(
        recall=recall,
        audit=audit,
        recall_backend=recall_backend,
        degraded=degraded,
        degraded_reason=degraded_reason,
    )
