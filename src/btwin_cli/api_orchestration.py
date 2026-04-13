"""Orchestration routes: records, promotions, admin, and workflow endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from btwin_cli.api_helpers import error_response, trace_id as _helper_trace_id
from btwin_core.agent_registry import AgentRegistry
from btwin_core.audit import AuditLogger
from btwin_core.event_bus import EventBus, SSEEvent
from btwin_core.guide_loader import GuideLoader
from btwin_core.indexer import CoreIndexer
from btwin_core.orchestration_models import (
    OrchestrationRecord,
    OrchestrationStatus,
    generate_record_id,
)
from btwin_core.pipeline_loader import PipelineLoader
from btwin_core.promotion_store import (
    PromotionActorRequiredError,
    PromotionItemNotFoundError,
    PromotionStore,
    PromotionTransitionError,
)
from btwin_core.promotion_worker import PromotionWorker
from btwin_core.runtime_ports import AuditEvent
from btwin_core.resource_paths import resolve_bundled_providers_path
from btwin_core.storage import Storage
from btwin_core.workflow_engine import WorkflowEngine
from btwin_core.workflow_gate import (
    apply_transition,
    validate_actor,
    validate_promotion_approval,
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateOrchestrationRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str = Field(alias="taskId")
    record_type: str = Field(alias="recordType")
    summary: str
    evidence: list[str]
    next_action: list[str] = Field(alias="nextAction")
    status: OrchestrationStatus
    author_agent: str = Field(alias="authorAgent")
    created_at: str = Field(alias="createdAt")
    project_id: str | None = Field(default=None, alias="projectId")


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    record_id: str = Field(alias="recordId")
    expected_version: int = Field(alias="expectedVersion", ge=1)
    from_agent: str = Field(alias="fromAgent")
    to_agent: str = Field(alias="toAgent")


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    record_id: str = Field(alias="recordId")
    expected_version: int = Field(alias="expectedVersion", ge=1)
    actor_agent: str = Field(alias="actorAgent")


class ReloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    actor_agent: str = Field(alias="actorAgent")
    override_path: str | None = Field(default=None, alias="overridePath")


class ProposePromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_record_id: str = Field(alias="sourceRecordId")
    proposed_by: str = Field(alias="proposedBy")


class ApprovePromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    actor_agent: str = Field(alias="actorAgent")


class RunPromotionBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    actor_agent: str = Field(alias="actorAgent")
    limit: int | None = Field(default=None, ge=1, le=1000)


class AssignAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str | None = None


class UpdateTaskStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str  # "in_progress", "done", "blocked", "escalated"
    actual_model: str | None = None


class QueueTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: str
    task_id: str


class ReorderQueueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_ids: list[str]


class NextTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actual_model: str | None = None


class RegisterAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    model: str
    alias: str | None = None
    capabilities: list[str] | None = None
    cli_config: dict | None = None
    reasoning_level: str | None = None
    bypass_permissions: bool | None = None
    memo: str | None = None


class UpdateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str | None = None
    capabilities: list[str] | None = None
    cli_config: dict | None = None
    model: str | None = None
    reasoning_level: str | None = None
    bypass_permissions: bool | None = None
    memo: str | None = None


class CreateWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    tasks: list[str] = Field(min_length=1)
    assigned_agents: list[str | None] | None = None


class CreateFromTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    template_id: str
    task_description: str
    cwd: str | None = None


class UpdateStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: str = Field(min_length=1)


class AttachGuideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    guide_id: str


class AgentAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    agent: str
    reason: str | None = None


class ConductorAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: str
    assignments: list[AgentAssignment]


class ConductorDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: str
    task_id: str
    decision: str  # "approve" or "request_fix"
    summary: str
    feedback: str | None = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_orchestration_router(
    storage: Storage,
    registry: AgentRegistry,
    promotion_store: PromotionStore,
    audit_logger: AuditLogger,
    indexer_factory,
    admin_token: str | None,
    workflow_engine: WorkflowEngine,
    runtime_adapters=None,
    event_bus: EventBus | None = None,
    conductor_loop=None,
    terminal_manager=None,
) -> APIRouter:
    router = APIRouter()

    _IDEMPOTENCY_CACHE_MAX = 1000
    idempotency_cache: OrderedDict[str, dict[str, str]] = OrderedDict()

    # -- internal helpers ---------------------------------------------------

    def _trace_id() -> str:
        return _helper_trace_id()

    def _error(status_code: int, error_code: str, message: str, details: dict[str, object] | None = None) -> JSONResponse:
        return error_response(status_code, error_code, message, details)

    def _publish_agent_event(name: str) -> None:
        if event_bus is not None:
            event_bus.publish(SSEEvent(type="agent_updated", resource_id=name))

    def _audit(event_type: str, payload: dict[str, object]) -> None:
        if runtime_adapters is not None:
            runtime_adapters.audit.append(
                AuditEvent(
                    event_type=event_type,
                    actor=str(payload.get("actorAgent") or payload.get("actor") or "system"),
                    trace_id=_trace_id(),
                    doc_version=int(payload.get("docVersion") or 0),
                    checksum=str(payload.get("checksum") or "n/a"),
                    payload=payload,
                )
            )

    def _require_admin_token_if_configured(x_admin_token: str | None) -> JSONResponse | None:
        from btwin_cli.api_helpers import require_admin_token
        return require_admin_token(x_admin_token, admin_token)

    def _require_main_admin(actor: str, x_admin_token: str | None) -> JSONResponse | None:
        from btwin_cli.api_helpers import require_main_admin
        return require_main_admin(actor, x_admin_token, admin_token, registry_agents=registry.agents)

    def _payload_hash(payload: dict[str, object]) -> str:
        normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _same_tuple(record: OrchestrationRecord, req: CreateOrchestrationRecordRequest) -> bool:
        return (
            record.task_id == req.task_id
            and record.status == req.status
            and record.author_agent == req.author_agent
        )

    def _indexer() -> CoreIndexer:
        return indexer_factory()

    def _enforce_integrity_gate(*, record_id: str, endpoint: str, actor: str) -> JSONResponse | None:
        doc = storage.orchestration_index_doc_info(record_id)
        if doc is None:
            return _error(404, "RECORD_NOT_FOUND", "orchestration record not found", {"recordId": record_id})

        idx = _indexer()
        existing = idx.manifest.get(doc["doc_id"])
        already_healthy = (
            existing is not None
            and existing.status == "indexed"
            and existing.checksum == doc["checksum"]
        )
        if not already_healthy:
            idx.mark_pending(
                doc_id=doc["doc_id"],
                path=doc["path"],
                record_type="collab",
                checksum=doc["checksum"],
            )

        integrity = idx.verify_doc_integrity(doc["doc_id"])
        repair_attempts = 0
        max_retries = 2
        last_repair: dict[str, object] | None = None

        while not integrity.get("ok") and repair_attempts < max_retries:
            last_repair = idx.repair(doc["doc_id"])
            repair_attempts += 1
            integrity = idx.verify_doc_integrity(doc["doc_id"])

        if integrity.get("ok"):
            return None

        details = {
            "recordId": record_id,
            "docId": doc["doc_id"],
            "integrity": integrity,
            "repairAttempts": repair_attempts,
            "lastRepair": last_repair or {},
        }
        _audit(
            "gate_rejected",
            {
                "endpoint": endpoint,
                "errorCode": "INTEGRITY_GATE_FAILED",
                "recordId": record_id,
                "actorAgent": actor,
                "details": details,
            },
        )
        return _error(409, "INTEGRITY_GATE_FAILED", "index integrity gate failed", details)

    # -----------------------------------------------------------------------
    # Orchestration record routes
    # -----------------------------------------------------------------------

    @router.post("/api/collab/records")
    def create_record(payload: CreateOrchestrationRecordRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        allowed = registry.agents
        actor_decision = validate_actor(payload.author_agent, allowed)
        if not actor_decision.ok:
            return _error(403, "FORBIDDEN", "author agent is not allowed", actor_decision.details)

        if payload.record_type != "collab":
            return _error(422, "INVALID_SCHEMA", "recordType must be collab")

        request_payload = payload.model_dump(by_alias=True, mode="json", exclude={"project_id"})
        request_hash = _payload_hash(request_payload)

        if idempotency_key:
            cached = idempotency_cache.get(idempotency_key)
            if cached:
                if cached["payload_hash"] != request_hash:
                    return _error(409, "DUPLICATE_RECORD", "idempotency key reused with different payload")

                existing = storage.read_orchestration_record(cached["record_id"])
                if existing is not None:
                    return {
                        "recordId": existing.record_id,
                        "status": existing.status,
                        "version": existing.version,
                        "idempotent": True,
                    }

        for existing in storage.list_orchestration_records():
            if _same_tuple(existing, payload):
                existing_payload = {
                    "taskId": existing.task_id,
                    "recordType": existing.record_type,
                    "summary": existing.summary,
                    "evidence": existing.evidence,
                    "nextAction": existing.next_action,
                    "status": existing.status,
                    "authorAgent": existing.author_agent,
                    "createdAt": existing.created_at.isoformat(),
                }
                if _payload_hash(existing_payload) != request_hash:
                    return _error(
                        409,
                        "DUPLICATE_RECORD",
                        "same taskId+status+authorAgent exists with different payload",
                        {
                            "taskId": payload.task_id,
                            "status": payload.status,
                            "authorAgent": payload.author_agent,
                        },
                    )
                return _error(
                    409,
                    "DUPLICATE_RECORD",
                    "same taskId+status+authorAgent already exists",
                    {
                        "recordId": existing.record_id,
                    },
                )

        try:
            record = OrchestrationRecord.model_validate(
                {
                    **request_payload,
                    "recordId": generate_record_id(),
                    "version": 1,
                }
            )
        except ValidationError as exc:
            return _error(422, "INVALID_SCHEMA", "orchestration record validation failed", {"issues": exc.errors()})

        storage.save_orchestration_record(record, project=payload.project_id)

        if idempotency_key:
            idempotency_cache[idempotency_key] = {
                "payload_hash": request_hash,
                "record_id": record.record_id,
            }
            if len(idempotency_cache) >= _IDEMPOTENCY_CACHE_MAX:
                idempotency_cache.popitem(last=False)

        return JSONResponse(
            status_code=201,
            content={
                "recordId": record.record_id,
                "status": record.status,
                "version": record.version,
                "idempotent": False,
            },
        )

    @router.get("/api/collab/records")
    def list_records(status: str | None = None, authorAgent: str | None = None, taskId: str | None = None, projectId: str | None = None):
        records = storage.list_orchestration_records(project=projectId)
        filtered: list[dict[str, object]] = []

        for r in records:
            if status and status != "all" and r.status != status:
                continue
            if authorAgent and r.author_agent != authorAgent:
                continue
            if taskId and r.task_id != taskId:
                continue
            filtered.append(r.model_dump(by_alias=True, mode="json"))

        return {"items": filtered}

    @router.get("/api/collab/records/{record_id}")
    def get_record(record_id: str):
        doc = storage.read_orchestration_record_document(record_id)
        if doc is None:
            return _error(404, "RECORD_NOT_FOUND", "orchestration record not found", {"recordId": record_id})
        return doc

    @router.post("/api/collab/handoff")
    def handoff(payload: HandoffRequest, x_actor_agent: str | None = Header(default=None, alias="X-Actor-Agent")):
        actor = x_actor_agent or payload.from_agent

        actor_decision = validate_actor(actor, registry.agents)
        if not actor_decision.ok:
            return _error(403, "FORBIDDEN", actor_decision.message or "forbidden", actor_decision.details)
        if actor != payload.from_agent:
            return _error(403, "FORBIDDEN", "actor must match fromAgent", {"actorAgent": actor, "fromAgent": payload.from_agent})
        if not registry.is_allowed(payload.to_agent):
            return _error(403, "FORBIDDEN", "toAgent is not allowed", {"toAgent": payload.to_agent})

        record = storage.read_orchestration_record(payload.record_id)
        if record is None:
            return _error(404, "RECORD_NOT_FOUND", "orchestration record not found", {"recordId": payload.record_id})
        if record.author_agent != payload.from_agent:
            return _error(
                403,
                "FORBIDDEN",
                "fromAgent is not current record owner",
                {"recordOwner": record.author_agent, "fromAgent": payload.from_agent},
            )

        integrity_error = _enforce_integrity_gate(record_id=payload.record_id, endpoint="/api/collab/handoff", actor=actor)
        if integrity_error is not None:
            return integrity_error

        decision = apply_transition(record, "handed_off", payload.expected_version)
        if not decision.ok:
            _audit(
                "gate_rejected",
                {
                    "endpoint": "/api/collab/handoff",
                    "errorCode": decision.error_code or "GATE_REJECTED",
                    "recordId": payload.record_id,
                    "actorAgent": actor,
                    "details": decision.details,
                },
            )
            return _error(409, decision.error_code or "GATE_REJECTED", decision.message, decision.details)

        if decision.idempotent:
            _audit(
                "gate_handoff_succeeded",
                {
                    "recordId": record.record_id,
                    "actorAgent": actor,
                    "fromStatus": record.status,
                    "toStatus": record.status,
                    "version": record.version,
                    "idempotent": True,
                },
            )
            return {
                "recordId": record.record_id,
                "status": record.status,
                "version": record.version,
                "idempotent": True,
            }

        updated = storage.update_orchestration_record(
            payload.record_id,
            status=decision.status or "handed_off",
            version=decision.version or record.version,
            author_agent=payload.to_agent,
        )
        if updated is None:
            return _error(404, "RECORD_NOT_FOUND", "orchestration record not found", {"recordId": payload.record_id})

        _audit(
            "gate_handoff_succeeded",
            {
                "recordId": updated.record_id,
                "actorAgent": actor,
                "fromStatus": record.status,
                "toStatus": updated.status,
                "version": updated.version,
                "idempotent": False,
            },
        )
        return {
            "recordId": updated.record_id,
            "status": updated.status,
            "version": updated.version,
            "idempotent": False,
        }

    @router.post("/api/collab/complete")
    def complete(payload: CompleteRequest, x_actor_agent: str | None = Header(default=None, alias="X-Actor-Agent")):
        actor = x_actor_agent or payload.actor_agent
        actor_decision = validate_actor(actor, registry.agents)
        if not actor_decision.ok:
            return _error(403, "FORBIDDEN", actor_decision.message or "forbidden", actor_decision.details)
        if actor != payload.actor_agent:
            return _error(403, "FORBIDDEN", "actor must match actorAgent", {"actorAgent": actor})

        record = storage.read_orchestration_record(payload.record_id)
        if record is None:
            return _error(404, "RECORD_NOT_FOUND", "orchestration record not found", {"recordId": payload.record_id})
        if record.author_agent != actor:
            return _error(
                403,
                "FORBIDDEN",
                "actor is not current record owner",
                {"recordOwner": record.author_agent, "actorAgent": actor},
            )

        integrity_error = _enforce_integrity_gate(record_id=payload.record_id, endpoint="/api/collab/complete", actor=actor)
        if integrity_error is not None:
            return integrity_error

        decision = apply_transition(record, "completed", payload.expected_version)
        if not decision.ok:
            _audit(
                "gate_rejected",
                {
                    "endpoint": "/api/collab/complete",
                    "errorCode": decision.error_code or "GATE_REJECTED",
                    "recordId": payload.record_id,
                    "actorAgent": actor,
                    "details": decision.details,
                },
            )
            return _error(409, decision.error_code or "GATE_REJECTED", decision.message, decision.details)

        if decision.idempotent:
            _audit(
                "gate_complete_succeeded",
                {
                    "recordId": record.record_id,
                    "actorAgent": actor,
                    "fromStatus": record.status,
                    "toStatus": record.status,
                    "version": record.version,
                    "idempotent": True,
                },
            )
            return {
                "recordId": record.record_id,
                "status": record.status,
                "version": record.version,
                "idempotent": True,
            }

        updated = storage.update_orchestration_record(payload.record_id, status=decision.status or "completed", version=decision.version or record.version)
        if updated is None:
            return _error(404, "RECORD_NOT_FOUND", "orchestration record not found", {"recordId": payload.record_id})

        _audit(
            "gate_complete_succeeded",
            {
                "recordId": updated.record_id,
                "actorAgent": actor,
                "fromStatus": record.status,
                "toStatus": updated.status,
                "version": updated.version,
                "idempotent": False,
            },
        )
        return {
            "recordId": updated.record_id,
            "status": updated.status,
            "version": updated.version,
            "idempotent": False,
        }

    # -----------------------------------------------------------------------
    # Promotion routes
    # -----------------------------------------------------------------------

    @router.post("/api/promotions/propose")
    def propose_promotion(payload: ProposePromotionRequest, x_actor_agent: str | None = Header(default=None, alias="X-Actor-Agent")):
        actor = x_actor_agent or payload.proposed_by

        actor_decision = validate_actor(actor, registry.agents)
        if not actor_decision.ok:
            return _error(403, "FORBIDDEN", actor_decision.message or "forbidden", actor_decision.details)
        if actor != payload.proposed_by:
            return _error(403, "FORBIDDEN", "actor must match proposedBy", {"actorAgent": actor, "proposedBy": payload.proposed_by})

        if storage.read_orchestration_record(payload.source_record_id) is None:
            return _error(404, "RECORD_NOT_FOUND", "source orchestration record not found", {"sourceRecordId": payload.source_record_id})

        item = promotion_store.enqueue(source_record_id=payload.source_record_id, proposed_by=payload.proposed_by)
        _audit(
            "promotion_proposed",
            {
                "itemId": item.item_id,
                "sourceRecordId": item.source_record_id,
                "proposedBy": item.proposed_by,
            },
        )
        return JSONResponse(
            status_code=201,
            content={
                "itemId": item.item_id,
                "sourceRecordId": item.source_record_id,
                "status": item.status,
                "proposedBy": item.proposed_by,
                "proposedAt": item.proposed_at.isoformat(),
            },
        )

    @router.get("/api/promotions")
    def list_promotions(status: str | None = None):
        items = promotion_store.list_items(status=status if status else None)
        return {
            "items": [
                {
                    "itemId": item.item_id,
                    "sourceRecordId": item.source_record_id,
                    "status": item.status,
                    "proposedBy": item.proposed_by,
                    "proposedAt": item.proposed_at.isoformat(),
                    "approvedBy": item.approved_by,
                    "approvedAt": item.approved_at.isoformat() if item.approved_at else None,
                    "queuedAt": item.queued_at.isoformat() if item.queued_at else None,
                    "promotedAt": item.promoted_at.isoformat() if item.promoted_at else None,
                }
                for item in items
            ]
        }

    @router.post("/api/promotions/{item_id}/approve")
    def approve_promotion(
        item_id: str,
        payload: ApprovePromotionRequest,
        x_actor_agent: str | None = Header(default=None, alias="X-Actor-Agent"),
    ):
        actor = x_actor_agent or payload.actor_agent

        actor_decision = validate_actor(actor, registry.agents)
        if not actor_decision.ok:
            return _error(403, "FORBIDDEN", actor_decision.message or "forbidden", actor_decision.details)
        if actor != payload.actor_agent:
            return _error(403, "FORBIDDEN", "actor must match actorAgent", {"actorAgent": actor})

        approval_decision = validate_promotion_approval(actor)
        if not approval_decision.ok:
            return _error(403, "FORBIDDEN", approval_decision.message or "forbidden", approval_decision.details)

        try:
            item = promotion_store.set_status(item_id, "approved", actor=actor)
        except PromotionItemNotFoundError:
            return _error(404, "PROMOTION_NOT_FOUND", "promotion item not found", {"itemId": item_id})
        except PromotionActorRequiredError:
            return _error(422, "INVALID_SCHEMA", "actor is required for approval")
        except PromotionTransitionError as exc:
            return _error(409, "INVALID_STATE_TRANSITION", str(exc), {"itemId": item_id})

        _audit(
            "promotion_approved",
            {
                "itemId": item.item_id,
                "approvedBy": item.approved_by or actor,
            },
        )
        return {
            "itemId": item.item_id,
            "status": item.status,
            "approvedBy": item.approved_by,
            "approvedAt": item.approved_at.isoformat() if item.approved_at else None,
        }

    @router.post("/api/promotions/run-batch")
    def run_promotions_batch(
        payload: RunPromotionBatchRequest,
        x_actor_agent: str | None = Header(default=None, alias="X-Actor-Agent"),
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    ):
        actor = x_actor_agent or payload.actor_agent

        actor_decision = validate_actor(actor, registry.agents)
        if not actor_decision.ok:
            return _error(403, "FORBIDDEN", actor_decision.message or "forbidden", actor_decision.details)
        if actor != payload.actor_agent:
            return _error(403, "FORBIDDEN", "actor must match actorAgent", {"actorAgent": actor})

        approval_decision = validate_promotion_approval(actor)
        if not approval_decision.ok:
            return _error(403, "FORBIDDEN", approval_decision.message or "forbidden", approval_decision.details)

        if not admin_token:
            return _error(403, "FORBIDDEN", "batch run is disabled (no admin token configured)")
        if not x_admin_token or not hmac.compare_digest(x_admin_token, admin_token):
            return _error(403, "FORBIDDEN", "admin token is required")

        worker = PromotionWorker(storage=storage, promotion_store=promotion_store, indexer=_indexer())
        result = worker.run_once(limit=payload.limit)
        _audit(
            "promotion_batch_run",
            {
                "actorAgent": actor,
                "limit": payload.limit,
                **result,
            },
        )
        return result

    @router.get("/api/promotions/history")
    def promotions_history(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
        auth_error = _require_admin_token_if_configured(x_admin_token)
        if auth_error is not None:
            return auth_error
        return {"items": storage.list_promoted_entries()}

    # -----------------------------------------------------------------------
    # Admin routes
    # -----------------------------------------------------------------------

    @router.post("/api/admin/agents/reload")
    def reload_agents(
        payload: ReloadRequest,
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
        x_actor_agent: str | None = Header(default=None, alias="X-Actor-Agent"),
    ):
        actor = x_actor_agent or payload.actor_agent

        actor_decision = validate_actor(actor, registry.agents)
        if not actor_decision.ok:
            return _error(403, "FORBIDDEN", actor_decision.message or "forbidden", actor_decision.details)
        if actor != payload.actor_agent:
            return _error(403, "FORBIDDEN", "actor must match actorAgent", {"actorAgent": actor})

        if not admin_token:
            return _error(403, "FORBIDDEN", "admin reload is disabled (no admin token configured)")
        if not x_admin_token or not hmac.compare_digest(x_admin_token, admin_token):
            return _error(403, "FORBIDDEN", "admin token is required")

        if payload.override_path:
            resolved = Path(payload.override_path).expanduser().resolve()
            home_dir = Path.home().resolve()
            if not resolved.is_relative_to(home_dir):
                return _error(
                    400,
                    "INVALID_PATH",
                    "overridePath must resolve within the user home directory",
                )

        summary = registry.reload(payload.override_path)
        return {"ok": True, **summary}

    # -----------------------------------------------------------------------
    # Agent register / unregister
    # -----------------------------------------------------------------------

    def _find_in_progress_tasks(agent_name: str) -> list[dict]:
        """Find in_progress tasks assigned to a given agent across all workflows."""
        entries = workflow_engine._read_all_workflow_entries()
        tasks = []
        for entry in entries:
            tags = entry.get("tags", [])
            if "wf-type:task" not in tags:
                continue
            if entry.get("status") != "in_progress":
                continue
            if entry.get("assigned_agent") != agent_name:
                continue
            workflow_id = None
            for tag in tags:
                if tag.startswith("wf-id:"):
                    workflow_id = tag[len("wf-id:"):]
                    break
            tasks.append({
                "task_id": entry.get("record_id", ""),
                "name": entry.get("name", ""),
                "workflow_id": workflow_id or "",
            })
        return tasks

    @router.post("/api/agents/register")
    def register_agent(body: RegisterAgentRequest):
        from btwin_core.agent_store import AgentStore
        agent_store = AgentStore(storage.data_dir)
        info = agent_store.register(body.name, body.model, body.alias, body.capabilities, body.cli_config, body.reasoning_level, body.bypass_permissions, body.memo)

        in_progress = _find_in_progress_tasks(body.name)
        _publish_agent_event(body.name)
        return {
            "name": info["name"],
            "model": info["model"],
            "in_progress_tasks": in_progress,
        }

    @router.get("/api/agents/{name}")
    def get_agent_detail(name: str):
        from btwin_core.agent_store import AgentStore, sanitize_agent_for_output
        agent_store = AgentStore(storage.data_dir)
        agent = agent_store.get_agent(name)
        if agent is None:
            return _error(404, "AGENT_NOT_FOUND", "agent not found", {"name": name})
        agent = sanitize_agent_for_output(agent)
        # Infer status from workflow entries
        entries = workflow_engine._read_all_workflow_entries()
        has_tasks = False
        is_working = False
        for entry in entries:
            if "wf-type:task" not in entry.get("tags", []):
                continue
            if entry.get("assigned_agent") != name:
                continue
            has_tasks = True
            if entry.get("status") == "in_progress":
                is_working = True
        status = "working" if is_working else ("idle" if has_tasks else "registered")
        return {
            "name": name,
            "model": agent.get("model", ""),
            "alias": agent.get("alias"),
            "status": status,
            "last_seen": agent.get("last_seen", ""),
            "registered_at": agent.get("registered_at", ""),
            "capabilities": agent.get("capabilities", []),
            "cli_config": agent.get("cli_config"),
            "queue_length": len(agent.get("queue", [])),
            "reasoning_level": agent.get("reasoning_level"),
            "bypass_permissions": agent.get("bypass_permissions", False),
            "memo": agent.get("memo", ""),
        }

    @router.delete("/api/agents/{name}")
    def unregister_agent(name: str):
        if name == "_conductor":
            return _error(400, "SYSTEM_AGENT", "Cannot delete system conductor agent")

        from btwin_core.agent_store import AgentStore
        agent_store = AgentStore(storage.data_dir)

        warnings: list[str] = []
        in_progress = _find_in_progress_tasks(name)
        if in_progress:
            for t in in_progress:
                warnings.append(
                    f"task {t['task_id']} ('{t['name']}') still in_progress in workflow {t['workflow_id']}"
                )

        removed = agent_store.unregister(name)
        if not removed:
            return _error(404, "AGENT_NOT_FOUND", "agent not found", {"name": name})

        _publish_agent_event(name)
        return {"removed": True, "warnings": warnings}

    @router.patch("/api/agents/{name}")
    def update_agent(name: str, body: UpdateAgentRequest):
        from btwin_core.agent_store import AgentStore
        from btwin_core.agent_store import _UNSET
        from btwin_core.agent_store import sanitize_agent_for_output
        agent_store = AgentStore(storage.data_dir)
        # Only pass reasoning_level if it was explicitly included in the request body
        reasoning_level_arg = body.reasoning_level if body.model_fields_set and "reasoning_level" in body.model_fields_set else _UNSET
        memo_arg = body.memo if body.model_fields_set and "memo" in body.model_fields_set else _UNSET
        updated = agent_store.update_agent(name, body.alias, body.capabilities, body.cli_config, body.model, reasoning_level_arg, body.bypass_permissions, memo_arg)
        if updated is None:
            return _error(404, "AGENT_NOT_FOUND", "agent not found", {"name": name})
        _publish_agent_event(name)
        return sanitize_agent_for_output(updated)

    # -----------------------------------------------------------------------
    # Agent list
    # -----------------------------------------------------------------------

    @router.get("/api/agents/{agent_name}/tasks")
    def get_agent_tasks(agent_name: str):
        entries = workflow_engine._read_all_workflow_entries()
        tasks = []
        for entry in entries:
            tags = entry.get("tags", [])
            if "wf-type:task" not in tags:
                continue
            if entry.get("assigned_agent") != agent_name:
                continue
            # Extract workflow_id from tags
            workflow_id = None
            for tag in tags:
                if tag.startswith("wf-id:"):
                    workflow_id = tag[len("wf-id:"):]
                    break
            tasks.append({
                "task_id": entry.get("record_id", ""),
                "name": entry.get("name", ""),
                "status": entry.get("status", ""),
                "order": entry.get("order", 0),
                "workflow_id": workflow_id or "",
            })
        tasks.sort(key=lambda t: (t["workflow_id"], t["order"]))
        return {"tasks": tasks}

    @router.get("/api/agents")
    def list_agents():
        from btwin_core.agent_store import AgentStore, sanitize_agent_for_output
        agent_store = AgentStore(storage.data_dir)
        registered = [sanitize_agent_for_output(agent) for agent in agent_store.list_agents()]

        # Infer status from workflow tasks
        entries = workflow_engine._read_all_workflow_entries()

        # Build sets: which agents have in_progress tasks, which have any assigned tasks
        agents_working = set()
        agents_with_tasks = set()
        for entry in entries:
            tags = entry.get("tags", [])
            if "wf-type:task" not in tags:
                continue
            agent = entry.get("assigned_agent")
            if not agent:
                continue
            agents_with_tasks.add(agent)
            if entry.get("status") == "in_progress":
                agents_working.add(agent)

        result = []
        for agent in registered:
            name = agent["name"]
            if name in agents_working:
                status = "working"
            elif name in agents_with_tasks:
                status = "idle"
            else:
                status = "registered"
            queue = agent.get("queue", [])
            result.append({
                "name": name,
                "model": agent.get("model", ""),
                "alias": agent.get("alias"),
                "status": status,
                "last_seen": agent.get("last_seen", ""),
                "capabilities": agent.get("capabilities", []),
                "cli_config": agent.get("cli_config"),
                "queue_length": len(queue),
                "reasoning_level": agent.get("reasoning_level"),
                "bypass_permissions": agent.get("bypass_permissions", False),
                "memo": agent.get("memo", ""),
            })

        return {"agents": result}

    # -----------------------------------------------------------------------
    # Agent queue endpoints
    # -----------------------------------------------------------------------

    def _build_queue_response(agent_name: str, agent_store) -> list[dict]:
        """Build enriched queue items with task names resolved from workflow engine."""
        raw_queue = agent_store.get_queue(agent_name)
        result = []
        for idx, item in enumerate(raw_queue):
            task_entry = workflow_engine._find_entry(item["task_id"])
            result.append({
                "workflow_id": item["workflow_id"],
                "task_id": item["task_id"],
                "name": task_entry.get("name", "") if task_entry else "",
                "status": task_entry.get("status", "") if task_entry else "",
                "order": idx,
            })
        return result

    @router.get("/api/agents/{name}/queue")
    def get_agent_queue(name: str):
        from btwin_core.agent_store import AgentStore
        agent_store = AgentStore(storage.data_dir)
        queue = _build_queue_response(name, agent_store)
        return {"queue": queue}

    @router.post("/api/agents/{name}/queue")
    def enqueue_agent_task(name: str, body: QueueTaskRequest):
        from btwin_core.agent_store import AgentStore
        agent_store = AgentStore(storage.data_dir)

        # Validate task exists
        task_entry = workflow_engine._find_entry(body.task_id)
        if task_entry is None:
            return _error(404, "TASK_NOT_FOUND", "task not found", {"taskId": body.task_id})

        try:
            agent_store.enqueue_task(name, body.workflow_id, body.task_id)
        except ValueError as exc:
            return _error(404, "AGENT_NOT_FOUND", str(exc), {"agent": name})

        _publish_agent_event(name)
        queue = _build_queue_response(name, agent_store)
        return {"queue": queue}

    @router.delete("/api/agents/{name}/queue/{task_id}")
    def dequeue_agent_task(name: str, task_id: str):
        from btwin_core.agent_store import AgentStore
        agent_store = AgentStore(storage.data_dir)

        try:
            agent_store.dequeue_task(name, task_id)
        except ValueError as exc:
            return _error(404, "AGENT_NOT_FOUND", str(exc), {"agent": name})

        _publish_agent_event(name)
        queue = _build_queue_response(name, agent_store)
        return {"queue": queue}

    @router.patch("/api/agents/{name}/queue/reorder")
    def reorder_agent_queue(name: str, body: ReorderQueueRequest):
        from btwin_core.agent_store import AgentStore
        agent_store = AgentStore(storage.data_dir)

        if agent_store.get_agent(name) is None:
            return _error(404, "AGENT_NOT_FOUND", "agent not found", {"name": name})

        try:
            agent_store.reorder_queue(name, body.task_ids)
        except ValueError as exc:
            return _error(400, "REORDER_FAILED", str(exc))

        _publish_agent_event(name)
        queue = _build_queue_response(name, agent_store)
        return {"queue": queue}

    @router.post("/api/agents/{name}/next-task")
    def next_task(name: str, body: NextTaskRequest):
        from btwin_core.agent_store import AgentStore
        agent_store = AgentStore(storage.data_dir)

        result = workflow_engine.next_task_from_queue(name, agent_store, body.actual_model)
        if result is None:
            return _error(404, "NO_VIABLE_TASK", "no viable task in queue", {"agent": name})
        return result

    # -----------------------------------------------------------------------
    # Workflow endpoints (delegating to WorkflowEngine)
    # -----------------------------------------------------------------------

    @router.post("/api/workflows", status_code=201)
    def create_workflow(body: CreateWorkflowRequest):
        result = workflow_engine.create_workflow(name=body.name, task_names=body.tasks, assigned_agents=body.assigned_agents)
        return result

    @router.get("/api/workflows")
    def list_workflows():
        entries = workflow_engine._read_all_workflow_entries()
        workflows = []
        for entry in entries:
            tags = entry.get("tags", [])
            if "wf-type:epic" in tags:
                workflows.append({
                    "workflow_id": entry["record_id"],
                    "name": entry.get("name", ""),
                    "status": entry["status"],
                    "created_at": entry.get("created_at", ""),
                })
        return {"items": workflows}

    @router.get("/api/workflows/health")
    def workflows_health():
        entries = workflow_engine._read_all_workflow_entries()
        issues = []

        for entry in entries:
            tags = entry.get("tags", [])
            record_id = entry.get("record_id", "")
            name = entry.get("name", "")

            # Check escalated workflows
            if "wf-type:epic" in tags and entry.get("status") == "escalated":
                issues.append({
                    "type": "escalated",
                    "workflow_id": record_id,
                    "name": name,
                    "detail": "Workflow is escalated",
                })

            # Check blocked tasks
            if "wf-type:task" in tags and entry.get("status") == "blocked":
                wf_id_tag = next((t for t in tags if t.startswith("wf-id:")), None)
                wf_id = wf_id_tag.split(":", 1)[1] if wf_id_tag else ""
                issues.append({
                    "type": "blocked",
                    "workflow_id": wf_id,
                    "name": name,
                    "detail": f"Task '{name}' is blocked",
                })

        # Check stalled workflows (active but no in_progress task)
        for entry in entries:
            tags = entry.get("tags", [])
            if "wf-type:epic" not in tags or entry.get("status") != "active":
                continue
            wf_id = entry.get("record_id", "")
            tasks = [
                e for e in entries
                if "wf-type:task" in e.get("tags", [])
                and any(t.startswith(f"wf-id:{wf_id}") for t in e.get("tags", []))
            ]
            has_in_progress = any(t.get("status") == "in_progress" for t in tasks)
            all_done = all(t.get("status") == "done" for t in tasks)
            if tasks and not has_in_progress and not all_done:
                issues.append({
                    "type": "stalled",
                    "workflow_id": wf_id,
                    "name": entry.get("name", ""),
                    "detail": "Active workflow with no task in progress",
                })

        return {
            "ok": len(issues) == 0,
            "scope": "workflows",
            "status": "healthy" if len(issues) == 0 else "issues_found",
            "issues": issues,
        }

    @router.get("/api/workflows/{workflow_id}")
    def get_workflow(workflow_id: str):
        wf = workflow_engine.get_workflow(workflow_id)
        if wf is None:
            return _error(404, "WORKFLOW_NOT_FOUND", "workflow not found", {"workflowId": workflow_id})
        return wf

    @router.patch("/api/workflows/{workflow_id}/status")
    def update_workflow_status(workflow_id: str, body: UpdateStatusRequest):
        wf = workflow_engine.get_workflow(workflow_id)
        if wf is None:
            return _error(404, "WORKFLOW_NOT_FOUND", "workflow not found", {"workflowId": workflow_id})

        new_status = body.status
        if new_status == "cancelled":
            ok = workflow_engine.cancel_workflow(workflow_id)
            if not ok:
                return _error(409, "INVALID_STATE_TRANSITION", "cannot cancel workflow", {"workflowId": workflow_id})
            updated = workflow_engine.get_workflow(workflow_id)
            return updated

        return _error(422, "INVALID_SCHEMA", f"unsupported status transition: {new_status}", {"status": new_status})

    @router.get("/api/workflows/{workflow_id}/timeline")
    def workflow_timeline(workflow_id: str):
        wf = workflow_engine.get_workflow(workflow_id)
        if wf is None:
            return _error(404, "WORKFLOW_NOT_FOUND", "workflow not found", {"workflowId": workflow_id})
        events = workflow_engine.get_timeline(workflow_id)
        return {"events": events}

    @router.get("/api/workflows/{workflow_id}/tasks/{task_id}")
    def get_task_detail(workflow_id: str, task_id: str):
        wf = workflow_engine.get_workflow(workflow_id)
        if wf is None:
            return _error(404, "WORKFLOW_NOT_FOUND", "workflow not found", {"workflowId": workflow_id})

        # Find task in workflow
        task_match = None
        for t in wf.get("tasks", []):
            if t["task_id"] == task_id:
                task_match = t
                break

        if task_match is None:
            return _error(404, "TASK_NOT_FOUND", "task not found", {"taskId": task_id})

        # Get raw markdown content from disk
        entry = workflow_engine._find_entry(task_id)
        raw_content = ""
        if entry and "_path" in entry:
            try:
                raw_content = Path(entry["_path"]).read_text()
            except OSError:
                raw_content = ""

        actual_model = entry.get("actual_model") if entry else None

        return {
            "task_id": task_id,
            "name": task_match["name"],
            "status": task_match["status"],
            "order": task_match["order"],
            "raw_content": raw_content,
            "actual_model": actual_model,
            "attached_guides": entry.get("attached_guides", []) if entry else [],
        }

    @router.patch("/api/workflows/{workflow_id}/tasks/{task_id}/agent")
    def assign_task_agent(workflow_id: str, task_id: str, body: AssignAgentRequest):
        wf = workflow_engine.get_workflow(workflow_id)
        if wf is None:
            return _error(404, "WORKFLOW_NOT_FOUND", "workflow not found", {"workflowId": workflow_id})
        task_match = next((t for t in wf["tasks"] if t["task_id"] == task_id), None)
        if task_match is None:
            return _error(404, "TASK_NOT_FOUND", "task not found", {"taskId": task_id})
        ok = workflow_engine.assign_agent(task_id, body.agent)
        if not ok:
            return _error(500, "ASSIGN_FAILED", "failed to assign agent", {"taskId": task_id})
        return {"task_id": task_id, "assigned_agent": body.agent}

    @router.patch("/api/workflows/{workflow_id}/tasks/{task_id}/status")
    async def update_task_status(workflow_id: str, task_id: str, body: UpdateTaskStatusRequest):
        wf = workflow_engine.get_workflow(workflow_id)
        if wf is None:
            return _error(404, "WORKFLOW_NOT_FOUND", "workflow not found", {"workflowId": workflow_id})
        task_match = next((t for t in wf["tasks"] if t["task_id"] == task_id), None)
        if task_match is None:
            return _error(404, "TASK_NOT_FOUND", "task not found", {"taskId": task_id})

        new_status = body.status
        result = None

        if new_status == "in_progress":
            result = workflow_engine.start_next_task(workflow_id, actual_model=body.actual_model)
            if result is None or result["task_id"] != task_id:
                return _error(409, "INVALID_STATE_TRANSITION", "cannot start this task", {"taskId": task_id})
        elif new_status == "done":
            # Resolve assigned agent for auto-advance from queue
            import asyncio
            from btwin_core.agent_store import AgentStore
            _agent_store = AgentStore(storage.data_dir)
            _agent_name = task_match.get("assigned_agent")
            result = workflow_engine.complete_task(
                workflow_id, task_id,
                agent_store=_agent_store if _agent_name else None,
                agent_name=_agent_name,
            )
            if result is not None and conductor_loop is not None and terminal_manager is not None:
                asyncio.create_task(
                    conductor_loop.on_task_completed(
                        workflow_id, task_id,
                        workflow_engine=workflow_engine,
                        agent_store=_agent_store,
                        terminal_manager=terminal_manager,
                        storage_data_dir=storage.data_dir,
                    )
                )
        elif new_status == "escalated":
            result = workflow_engine.escalate_task(workflow_id, task_id)
        elif new_status == "blocked":
            result = workflow_engine.block_task(workflow_id, task_id)
        else:
            return _error(422, "INVALID_SCHEMA", f"unsupported status: {new_status}", {"status": new_status})

        if result is None:
            return _error(409, "INVALID_STATE_TRANSITION", "transition failed", {"taskId": task_id})
        return result

    # -- conductor endpoints ------------------------------------------------

    @router.post("/api/conductor/assign")
    def conductor_assign(body: ConductorAssignRequest):
        results = []
        for assignment in body.assignments:
            workflow_engine.assign_agent(assignment.task_id, assignment.agent)
            workflow_engine._add_timeline_event(
                body.workflow_id,
                f"conductor assigned agent '{assignment.agent}' to task {assignment.task_id}"
                + (f" — {assignment.reason}" if assignment.reason else ""),
            )
            results.append({
                "task_id": assignment.task_id,
                "agent": assignment.agent,
                "assigned": True,
            })
        return {"assignments": results}

    @router.post("/api/conductor/decision")
    def conductor_decision(body: ConductorDecisionRequest):
        if body.decision not in ("approve", "request_fix"):
            return _error(400, "INVALID_DECISION", "decision must be 'approve' or 'request_fix'")

        # Record the decision in workflow timeline
        workflow_engine._add_timeline_event(
            body.workflow_id,
            f"conductor decision: {body.decision} for task {body.task_id} — {body.summary}",
        )

        if body.decision == "approve":
            # Start next pending task
            result = workflow_engine.start_next_task(body.workflow_id)
            return {
                "decision": "approve",
                "next_task": result,
            }
        else:  # request_fix
            # Find the reviewed task
            tasks = workflow_engine.list_tasks(body.workflow_id)
            reviewed_task = next((t for t in tasks if t["task_id"] == body.task_id), None)

            if reviewed_task is None:
                return _error(404, "TASK_NOT_FOUND", "task not found")

            # Find the implementation task: the task with the highest order
            # value still below the reviewed task's order
            impl_task = None
            for t in tasks:
                if t.get("order", 0) < reviewed_task.get("order", 0):
                    impl_task = t

            implementer = impl_task.get("assigned_agent") if impl_task else None

            # Insert a Fix task immediately after the current (review) task
            fix_task = workflow_engine.insert_task(
                body.workflow_id,
                name=f"Fix: {body.summary[:50]}",
                after_task_id=body.task_id,
                assigned_agent=implementer,
            )

            if fix_task:
                workflow_engine._add_timeline_event(
                    body.workflow_id,
                    f"fix requested: {body.feedback or body.summary}",
                )

            return {
                "decision": "request_fix",
                "fix_task": fix_task,
                "feedback": body.feedback,
                "summary": body.summary,
            }

    # -- providers config ---------------------------------------------------

    @router.get("/api/providers")
    def get_providers():
        config_path = storage.data_dir / "providers.json"
        if config_path.exists():
            return json.loads(config_path.read_text(encoding="utf-8"))
        bundled = resolve_bundled_providers_path()
        if bundled is not None:
            return json.loads(bundled.read_text(encoding="utf-8"))
        return {"providers": [], "capabilities": []}

    # -- pipeline templates -------------------------------------------------

    @router.get("/api/pipelines")
    def list_pipelines():
        loader = PipelineLoader(storage.data_dir)
        return {"templates": loader.list_templates()}

    @router.get("/api/pipelines/{template_id}")
    def get_pipeline(template_id: str):
        loader = PipelineLoader(storage.data_dir)
        template = loader.get_template(template_id)
        if template is None:
            return _error(404, "TEMPLATE_NOT_FOUND", "template not found", {"id": template_id})
        return template

    @router.post("/api/workflows/from-template")
    async def create_workflow_from_template(body: CreateFromTemplateRequest):
        loader = PipelineLoader(storage.data_dir)
        template = loader.get_template(body.template_id)
        if template is None:
            return _error(404, "TEMPLATE_NOT_FOUND", "template not found", {"id": body.template_id})

        # Build task names from template stages
        task_names = [stage["name"] for stage in template["stages"]]

        # Create workflow using existing engine
        result = workflow_engine.create_workflow(
            name=f"{template['name']}: {body.task_description[:50]}",
            task_names=task_names,
        )

        workflow_id = result["workflow_id"]
        tasks = result["tasks"]

        # Auto-assign agents based on role -> capability matching
        import asyncio
        from btwin_core.agent_store import AgentStore
        agent_store = AgentStore(storage.data_dir)
        agents = agent_store.list_agents()

        for i, stage in enumerate(template["stages"]):
            if i >= len(tasks):
                break
            task_id = tasks[i]["task_id"]
            role = stage["role"]

            # Find agent with matching capability
            matched_agent = None
            for agent in agents:
                caps = agent.get("capabilities", [])
                if role in caps:
                    matched_agent = agent["name"]
                    break

            if matched_agent:
                workflow_engine.assign_agent(task_id, matched_agent)

        # Re-fetch tasks to include agent assignments
        updated_tasks = workflow_engine.list_tasks(workflow_id)

        # Auto-start first task if it has an assigned agent
        first_task = updated_tasks[0] if updated_tasks else None
        if first_task and first_task.get("assigned_agent"):
            started = workflow_engine.start_next_task(workflow_id)
            if started and conductor_loop is not None and terminal_manager is not None:
                asyncio.create_task(
                    conductor_loop.dispatch_first_task(
                        workflow_id, workflow_engine,
                        AgentStore(storage.data_dir), terminal_manager,
                        storage_data_dir=storage.data_dir,
                    )
                )

        # Re-fetch to reflect started status
        updated_tasks = workflow_engine.list_tasks(workflow_id)
        return {
            "workflow_id": workflow_id,
            "tasks": updated_tasks,
            "template": template["id"],
        }

    # -- guide endpoints ----------------------------------------------------

    @router.get("/api/guides")
    def list_guides():
        loader = GuideLoader(storage.data_dir)
        return {"guides": loader.list_guides()}

    @router.get("/api/guides/{guide_id}")
    def get_guide(guide_id: str):
        loader = GuideLoader(storage.data_dir)
        guide = loader.get_guide(guide_id)
        if guide is None:
            return _error(404, "GUIDE_NOT_FOUND", "guide not found", {"id": guide_id})
        return guide

    # -- guide attachment endpoints ----------------------------------------

    @router.post("/api/workflows/{workflow_id}/tasks/{task_id}/guides")
    def attach_guide(workflow_id: str, task_id: str, body: AttachGuideRequest):
        task = workflow_engine._find_entry(task_id)
        if task is None:
            return _error(404, "TASK_NOT_FOUND", "task not found", {"task_id": task_id})
        guides = list(task.get("attached_guides", []))
        if body.guide_id not in guides:
            guides.append(body.guide_id)
        workflow_engine._update_entry_frontmatter(task_id, {"attached_guides": guides})
        return {"attached_guides": guides}

    @router.delete("/api/workflows/{workflow_id}/tasks/{task_id}/guides/{guide_id}")
    def detach_guide(workflow_id: str, task_id: str, guide_id: str):
        task = workflow_engine._find_entry(task_id)
        if task is None:
            return _error(404, "TASK_NOT_FOUND", "task not found", {"task_id": task_id})
        guides = list(task.get("attached_guides", []))
        guides = [g for g in guides if g != guide_id]
        workflow_engine._update_entry_frontmatter(task_id, {"attached_guides": guides})
        return {"attached_guides": guides}

    return router
