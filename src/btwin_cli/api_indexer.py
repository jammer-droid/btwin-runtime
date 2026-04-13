"""Indexer routes for the B-TWIN API."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field

from btwin_cli.api_helpers import error_response, require_admin_token
from btwin_core.audit import AuditLogger
from btwin_core.indexer import CoreIndexer
from btwin_core.storage import Storage


class IndexerActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    actor_agent: str = Field(alias="actorAgent")
    limit: int | None = Field(default=None, ge=1, le=1000)
    doc_id: str | None = Field(default=None, alias="docId")


def create_indexer_router(
    indexer_factory: Callable[[], CoreIndexer],
    storage: Storage,
    admin_token: str | None,
    *,
    audit_fn: Callable[[str, dict[str, object]], None],
    require_main_admin_fn: Callable[[str, str | None], object | None],
    runtime_mode: str,
    runtime_adapters: object,
    audit_logger: AuditLogger,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/indexer/status")
    def indexer_status(projectId: str | None = None, x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
        auth_error = require_admin_token(x_admin_token, admin_token)
        if auth_error is not None:
            return auth_error
        return indexer_factory().status_summary(project=projectId)

    @router.get("/api/indexer/kpi")
    def indexer_kpi(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
        auth_error = require_admin_token(x_admin_token, admin_token)
        if auth_error is not None:
            return auth_error
        return indexer_factory().kpi_summary()

    @router.get("/api/ops/dashboard")
    def ops_dashboard(projectId: str | None = None, x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
        auth_error = require_admin_token(x_admin_token, admin_token)
        if auth_error is not None:
            return auth_error

        idx = indexer_factory()
        gate_violations = [
            row
            for row in audit_logger.tail(limit=200)
            if row.get("eventType") == "gate_rejected"
        ]
        return {
            "runtime": {
                "mode": runtime_mode,
                "attached": runtime_mode == "attached",
                "recallAdapter": runtime_adapters.recall_backend,
                "degraded": runtime_adapters.degraded,
                "degradedReason": runtime_adapters.degraded_reason,
            },
            "indexerStatus": idx.status_summary(project=projectId),
            "failureQueue": idx.failure_queue(limit=50),
            "repairHistory": idx.repair_history(limit=20),
            "gateViolations": gate_violations[-20:],
        }

    @router.post("/api/indexer/refresh")
    def indexer_refresh(
        payload: IndexerActionRequest,
        x_actor_agent: str | None = Header(default=None, alias="X-Actor-Agent"),
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    ):
        actor = x_actor_agent or payload.actor_agent
        auth_error = require_main_admin_fn(actor, x_admin_token)
        if auth_error is not None:
            return auth_error
        if actor != payload.actor_agent:
            return error_response(403, "FORBIDDEN", "actor must match actorAgent", {"actorAgent": actor})

        result = indexer_factory().refresh(limit=payload.limit)
        audit_fn("indexer_refresh", {"actorAgent": actor, **result})
        return result

    @router.post("/api/indexer/reconcile")
    def indexer_reconcile(
        payload: IndexerActionRequest,
        x_actor_agent: str | None = Header(default=None, alias="X-Actor-Agent"),
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    ):
        actor = x_actor_agent or payload.actor_agent
        auth_error = require_main_admin_fn(actor, x_admin_token)
        if auth_error is not None:
            return auth_error
        if actor != payload.actor_agent:
            return error_response(403, "FORBIDDEN", "actor must match actorAgent", {"actorAgent": actor})

        result = indexer_factory().reconcile()
        audit_fn("indexer_reconcile", {"actorAgent": actor, **result})
        return result

    @router.post("/api/indexer/repair")
    def indexer_repair(
        payload: IndexerActionRequest,
        x_actor_agent: str | None = Header(default=None, alias="X-Actor-Agent"),
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    ):
        actor = x_actor_agent or payload.actor_agent
        auth_error = require_main_admin_fn(actor, x_admin_token)
        if auth_error is not None:
            return auth_error
        if actor != payload.actor_agent:
            return error_response(403, "FORBIDDEN", "actor must match actorAgent", {"actorAgent": actor})
        if not payload.doc_id:
            return error_response(422, "INVALID_SCHEMA", "docId is required")

        result = indexer_factory().repair(payload.doc_id)
        audit_fn("indexer_repair", {"actorAgent": actor, **result})
        return result

    return router
