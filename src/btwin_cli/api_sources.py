"""Source management routes for the B-TWIN API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from btwin_cli.api_helpers import error_response
from btwin_core.event_bus import EventBus, SSEEvent
from btwin_core.sources import SourceRegistry


class SourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str
    name: str | None = None
    enabled: bool = True


class SourceScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    roots: list[str]
    max_depth: int = Field(default=4, alias="maxDepth", ge=1, le=12)


class SourceRegisterCandidatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    paths: list[str]


class SourcePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str | None = None
    enabled: bool | None = None


def _source_payload(source, source_registry: SourceRegistry) -> dict[str, object]:
    return {
        "id": source_registry.source_id(source),
        "name": source.name,
        "path": source.path,
        "enabled": source.enabled,
        "entryCount": source.entry_count,
        "lastScannedAt": source.last_scanned_at,
    }


def _list_sources(source_registry: SourceRegistry, *, refresh: bool = False) -> list[dict[str, object]]:
    source_registry.ensure_global_default()
    sources = source_registry.refresh_entry_counts() if refresh else source_registry.load()
    return [_source_payload(source, source_registry) for source in sources]


def create_sources_router(source_registry: SourceRegistry, *, event_bus: EventBus | None = None) -> APIRouter:
    router = APIRouter()

    def _publish(resource_id: str = "all") -> None:
        if event_bus is not None:
            event_bus.publish(SSEEvent(type="source_updated", resource_id=resource_id))

    @router.get("/api/sources")
    def list_sources(refresh: bool = False):
        return {"items": _list_sources(source_registry, refresh=refresh)}

    @router.post("/api/sources")
    def create_source(req: SourceCreateRequest):
        source = source_registry.add_source(req.path, name=req.name, enabled=req.enabled)
        _publish(source_registry.source_id(source))
        return {"item": _source_payload(source, source_registry)}

    @router.post("/api/sources/scan")
    def scan_sources(req: SourceScanRequest):
        existing_paths = {str(SourceRegistry.canonical_path(source.path)) for source in source_registry.load()}
        found = source_registry.scan_for_btwin_dirs([Path(root) for root in req.roots], max_depth=req.max_depth)
        return {
            "items": [
                {
                    "path": str(path),
                    "suggestedName": source_registry.suggested_name(path),
                    "alreadyRegistered": str(path) in existing_paths,
                }
                for path in found
            ]
        }

    @router.post("/api/sources/register-candidates")
    def register_source_candidates(req: SourceRegisterCandidatesRequest):
        items = []
        for raw_path in req.paths:
            source = source_registry.add_source(raw_path)
            items.append(_source_payload(source, source_registry))
        _publish()
        return {"items": items}

    @router.patch("/api/sources/{source_id}")
    def patch_source(source_id: str, req: SourcePatchRequest):
        source = source_registry.update_source(source_id, name=req.name, enabled=req.enabled)
        if source is None:
            return error_response(404, "SOURCE_NOT_FOUND", "source not found", {"sourceId": source_id})
        _publish(source_id)
        return {"item": _source_payload(source, source_registry)}

    @router.post("/api/sources/refresh")
    def refresh_sources():
        result = {"items": _list_sources(source_registry, refresh=True)}
        _publish()
        if event_bus is not None:
            event_bus.publish(SSEEvent(type="entry_updated", resource_id="all"))
        return result

    return router
