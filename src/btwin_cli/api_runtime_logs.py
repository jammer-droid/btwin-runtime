"""Runtime logs API routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from btwin_core.runtime_logging import RuntimeEventLogger


def create_runtime_logs_router(data_dir: Path) -> APIRouter:
    router = APIRouter()
    logger = RuntimeEventLogger(data_dir)

    @router.get("/api/runtime/logs")
    def get_runtime_logs(
        limit: int = Query(default=20, ge=1, le=1000),
        traceId: str | None = None,
        threadId: str | None = None,
        agentName: str | None = None,
    ):
        return {
            "events": logger.tail(
                limit=limit,
                trace_id=traceId,
                thread_id=threadId,
                agent_name=agentName,
            )
        }

    return router
