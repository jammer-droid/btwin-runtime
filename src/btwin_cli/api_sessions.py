"""Session routes for the B-TWIN API."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from btwin_core.btwin import BTwin
from btwin_core.event_bus import EventBus, SSEEvent


class SessionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    topic: str | None = None


class SessionEndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    summary: str | None = None
    tldr: str
    slug: str | None = None
    project_id: str | None = Field(default=None, alias="projectId")
    tags: list[str] | None = None
    subject_projects: list[str] | None = Field(default=None, alias="subjectProjects")


def create_sessions_router(btwin_factory: Callable[[], BTwin], *, event_bus: EventBus | None = None) -> APIRouter:
    router = APIRouter()

    @router.post("/api/sessions/start")
    def session_start(body: SessionStartRequest):
        result = btwin_factory().start_session(topic=body.topic)
        if event_bus is not None:
            event_bus.publish(SSEEvent(type="session_updated", resource_id="active"))
        return result

    @router.post("/api/sessions/end")
    def session_end(body: SessionEndRequest):
        result = btwin_factory().end_session(
            summary=body.summary,
            slug=body.slug,
            project=body.project_id,
            tags=body.tags,
            subject_projects=body.subject_projects,
            tldr=body.tldr,
        )
        if event_bus is not None:
            event_bus.publish(SSEEvent(type="session_updated", resource_id="active"))
        if result is None:
            return JSONResponse(status_code=200, content=None)
        return result

    @router.get("/api/sessions/status")
    def session_status():
        return btwin_factory().session_status()

    return router
