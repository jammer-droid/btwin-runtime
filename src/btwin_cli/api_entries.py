"""Entry routes for the B-TWIN API."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from btwin_cli.api_helpers import (
    collapse_ws,
    error_response,
    require_admin_token,
    split_markdown_document,
    truncate,
)
from btwin_core.btwin import BTwin
from btwin_core.sources import SourceRegistry
from btwin_core.storage import Storage


class EntryRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    content: str
    tldr: str
    topic: str | None = None
    project_id: str | None = Field(default=None, alias="projectId")
    tags: list[str] | None = None
    subject_projects: list[str] | None = Field(default=None, alias="subjectProjects")


class EntrySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    query: str
    n_results: int = Field(default=5, alias="nResults", ge=1, le=100)
    project_id: str | None = Field(default=None, alias="projectId")
    record_type: str | None = Field(default=None, alias="recordType")
    scope: Literal["project", "all"] = "project"


class ConvoRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    content: str
    tldr: str
    requested_by_user: bool = Field(default=False, alias="requestedByUser")
    topic: str | None = None
    project_id: str | None = Field(default=None, alias="projectId")
    tags: list[str] | None = None
    subject_projects: list[str] | None = Field(default=None, alias="subjectProjects")


class EntryImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    content: str
    tldr: str
    date: str
    slug: str
    tags: list[str] | None = None
    source_path: str | None = Field(default=None, alias="sourcePath")
    project_id: str | None = Field(default=None, alias="projectId")


class UpdateEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    record_id: str = Field(alias="recordId")
    content: str | None = None
    tags: list[str] | None = None
    subject_projects: list[str] | None = Field(default=None, alias="subjectProjects")
    related_records: list[str] | None = Field(default=None, alias="relatedRecords")
    derived_from: str | None = Field(default=None, alias="derivedFrom")
    contributor: str | None = None


def _entry_title(content: str, fallback: str, topic: str | None = None) -> str:
    if topic:
        cleaned_topic = collapse_ws(topic)
        if cleaned_topic:
            return cleaned_topic
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cleaned = stripped.lstrip("#").strip()
        if cleaned:
            return truncate(cleaned, 90)
    return fallback


def _entry_preview(content: str, *, title: str) -> str:
    compact = collapse_ws(content)
    if compact.startswith(title):
        compact = compact[len(title):].lstrip(" :-\u2014")
    return truncate(compact or title, 220)


def _entry_item_key(
    *,
    record_type: str,
    source_id: str,
    project: str,
    date: str | None = None,
    slug: str | None = None,
    record_id: str | None = None,
) -> str:
    base = [record_type, source_id, project]
    if record_type == "collab":
        base.append(record_id or "")
    else:
        base.extend([date or "", slug or ""])
    return "|".join(base)


def _public_entry_item(item: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in item.items()
        if not str(key).startswith("_")
    }


def create_entries_router(
    storage: Storage,
    source_registry: SourceRegistry,
    btwin_factory: Callable[[], BTwin],
    admin_token: str | None,
    data_dir: Path,
) -> APIRouter:
    router = APIRouter()

    def _entries_source_configs(*, include_disabled: bool = False) -> list[dict[str, object]]:
        local_path = SourceRegistry.canonical_path(data_dir)
        local_id = SourceRegistry.source_id(local_path)
        registered = source_registry.load()
        by_path = {
            SourceRegistry.canonical_path(source.path): source
            for source in registered
        }

        items: list[dict[str, object]] = []
        local_source = by_path.get(local_path)
        items.append(
            {
                "id": local_id,
                "name": local_source.name if local_source is not None else "current",
                "path": str(local_path),
                "enabled": True if local_source is None else local_source.enabled,
                "implicit": local_source is None,
            }
        )

        for source in registered:
            canonical = SourceRegistry.canonical_path(source.path)
            if canonical == local_path:
                continue
            if not include_disabled and not source.enabled:
                continue
            items.append(
                {
                    "id": SourceRegistry.source_id(source),
                    "name": source.name,
                    "path": str(canonical),
                    "enabled": source.enabled,
                    "implicit": False,
                }
            )

        return items

    def _collect_entries_catalog() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        source_configs = _entries_source_configs(include_disabled=False)
        items: list[dict[str, object]] = []

        for source in source_configs:
            source_path = Path(str(source["path"]))
            source_storage = storage if source_path == SourceRegistry.canonical_path(data_dir) else Storage(source_path)
            source_id = str(source["id"])
            source_name = str(source["name"])
            source_path_str = str(source["path"])

            for entry in source_storage.list_entries():
                project = str(entry.metadata.get("project") or "_global")
                title = _entry_title(
                    entry.content,
                    fallback=entry.slug,
                    topic=str(entry.metadata.get("topic") or "") or None,
                )
                created_at = str(entry.metadata.get("created_at") or f"{entry.date}T00:00:00+00:00")
                preview = _entry_preview(entry.content, title=title)
                tags = [str(tag) for tag in (entry.metadata.get("tags") or []) if str(tag).strip()]
                items.append(
                    {
                        "key": _entry_item_key(
                            record_type="entry",
                            source_id=source_id,
                            project=project,
                            date=entry.date,
                            slug=entry.slug,
                        ),
                        "recordType": "entry",
                        "title": title,
                        "summary": preview,
                        "preview": preview,
                        "date": entry.date,
                        "createdAt": created_at,
                        "slug": entry.slug,
                        "project": project,
                        "sourceId": source_id,
                        "sourceName": source_name,
                        "sourcePath": source_path_str,
                        "topic": entry.metadata.get("topic"),
                        "tags": tags,
                        "requestedByUser": None,
                        "taskId": None,
                        "recordId": None,
                        "status": None,
                        "authorAgent": None,
                        "kindLabel": "entry",
                        "_sortKey": created_at,
                        "_searchText": collapse_ws(
                            " ".join(
                                [
                                    source_name,
                                    project,
                                    entry.slug,
                                    title,
                                    preview,
                                    entry.content,
                                    " ".join(tags),
                                ]
                            )
                        ).lower(),
                    }
                )

            for convo in source_storage.list_convo_entries():
                project = str(convo.metadata.get("project") or "_global")
                topic = str(convo.metadata.get("topic") or "") or None
                title = _entry_title(convo.content, fallback=convo.slug, topic=topic)
                created_at = str(convo.metadata.get("created_at") or f"{convo.date}T00:00:00+00:00")
                preview = _entry_preview(convo.content, title=title)
                requested_by_user = bool(convo.metadata.get("requestedByUser") or False)
                items.append(
                    {
                        "key": _entry_item_key(
                            record_type="convo",
                            source_id=source_id,
                            project=project,
                            date=convo.date,
                            slug=convo.slug,
                        ),
                        "recordType": "convo",
                        "title": title,
                        "summary": preview,
                        "preview": preview,
                        "date": convo.date,
                        "createdAt": created_at,
                        "slug": convo.slug,
                        "project": project,
                        "sourceId": source_id,
                        "sourceName": source_name,
                        "sourcePath": source_path_str,
                        "topic": topic,
                        "tags": [],
                        "requestedByUser": requested_by_user,
                        "taskId": None,
                        "recordId": None,
                        "status": None,
                        "authorAgent": None,
                        "kindLabel": "conversation",
                        "_sortKey": created_at,
                        "_searchText": collapse_ws(
                            " ".join(
                                [
                                    source_name,
                                    project,
                                    convo.slug,
                                    title,
                                    preview,
                                    convo.content,
                                    "requested by user" if requested_by_user else "system memo",
                                ]
                            )
                        ).lower(),
                    }
                )

            for orch_rec in source_storage.list_orchestration_records():
                document = source_storage.read_orchestration_record_document(orch_rec.record_id)
                frontmatter = document.get("frontmatter", {}) if document else {}
                project = str(frontmatter.get("project") or "_global")
                preview = truncate(
                    " | ".join([
                        f"evidence: {', '.join(orch_rec.evidence)}",
                        f"next: {', '.join(orch_rec.next_action)}",
                    ]),
                    220,
                )
                items.append(
                    {
                        "key": _entry_item_key(
                            record_type="collab",
                            source_id=source_id,
                            project=project,
                            record_id=orch_rec.record_id,
                        ),
                        "recordType": "collab",
                        "title": orch_rec.summary,
                        "summary": preview,
                        "preview": preview,
                        "date": orch_rec.created_at.date().isoformat(),
                        "createdAt": orch_rec.created_at.isoformat(),
                        "slug": None,
                        "project": project,
                        "sourceId": source_id,
                        "sourceName": source_name,
                        "sourcePath": source_path_str,
                        "topic": None,
                        "tags": [],
                        "requestedByUser": None,
                        "taskId": orch_rec.task_id,
                        "recordId": orch_rec.record_id,
                        "status": orch_rec.status,
                        "authorAgent": orch_rec.author_agent,
                        "kindLabel": "collab",
                        "_sortKey": orch_rec.created_at.isoformat(),
                        "_searchText": collapse_ws(
                            " ".join(
                                [
                                    source_name,
                                    project,
                                    orch_rec.record_id,
                                    orch_rec.task_id,
                                    orch_rec.summary,
                                    " ".join(orch_rec.evidence),
                                    " ".join(orch_rec.next_action),
                                    orch_rec.author_agent,
                                    orch_rec.status,
                                ]
                            )
                        ).lower(),
                    }
                )

        items.sort(key=lambda item: str(item.get("_sortKey") or ""), reverse=True)
        return items, source_configs

    def _load_entry_detail(key: str) -> dict[str, object] | None:
        parts = key.split("|")
        if len(parts) < 4:
            return None

        record_type = parts[0]
        source_id = parts[1]
        project = parts[2] or "_global"
        source_lookup = {
            str(source["id"]): source
            for source in _entries_source_configs(include_disabled=True)
        }
        source = source_lookup.get(source_id)
        if source is None:
            return None

        source_path = Path(str(source["path"]))
        source_storage = storage if source_path == SourceRegistry.canonical_path(data_dir) else Storage(source_path)
        source_name = str(source["name"])

        if record_type in {"entry", "convo"}:
            if len(parts) != 5:
                return None
            date, slug = parts[3], parts[4]
            # Unified path: entries/{record_type}/{date}/{record_id}.md
            file_path = source_storage.entries_dir / record_type / date / f"{slug}.md"
            if not file_path.exists():
                # Fallback: legacy path entries/{project}/{date}/{slug}.md or entries/{project}/convo/{date}/{slug}.md
                base_dir = source_storage.project_dir(project)
                file_path = base_dir / date / f"{slug}.md"
                if record_type == "convo":
                    file_path = base_dir / "convo" / date / f"{slug}.md"
            if not file_path.exists():
                return None
            raw = file_path.read_text()
            frontmatter, content = split_markdown_document(raw)
            title = _entry_title(
                content,
                fallback=slug,
                topic=str(frontmatter.get("topic") or "") or None,
            )
            preview = _entry_preview(content, title=title)
            return {
                "key": key,
                "recordType": record_type,
                "title": title,
                "summary": preview,
                "content": content,
                "frontmatter": frontmatter,
                "path": str(file_path),
                "date": date,
                "slug": slug,
                "project": str(frontmatter.get("project") or project),
                "sourceId": source_id,
                "sourceName": source_name,
                "sourcePath": str(source_path),
            }

        if record_type == "collab":
            if len(parts) != 4:
                return None
            record_id = parts[3]
            document = source_storage.read_orchestration_record_document(record_id, project=project)
            if document is None:
                return None
            frontmatter = document.get("frontmatter", {})
            content = str(document.get("content") or "")
            return {
                "key": key,
                "recordType": "collab",
                "title": str(frontmatter.get("summary") or record_id),
                "summary": truncate(content, 220),
                "content": content,
                "frontmatter": frontmatter,
                "path": str(document.get("path") or ""),
                "date": str(frontmatter.get("createdAt") or "")[:10],
                "recordId": record_id,
                "project": str(frontmatter.get("project") or project),
                "sourceId": source_id,
                "sourceName": source_name,
                "sourcePath": str(source_path),
            }

        return None

    @router.get("/api/entries")
    def list_entries(
        recordType: str | None = None,
        q: str | None = None,
        sourceId: str | None = None,
        tags: str | None = None,
        dateFrom: str | None = None,
        dateTo: str | None = None,
        limit: int = 200,
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    ):
        auth_error = require_admin_token(x_admin_token, admin_token)
        if auth_error is not None:
            return auth_error

        items, sources = _collect_entries_catalog()
        total_available = len(items)

        # Apply source/date/search/tag filters BEFORE typeCounts
        if sourceId not in (None, "", "all"):
            items = [item for item in items if item.get("sourceId") == sourceId]

        if dateFrom:
            items = [item for item in items if str(item.get("date") or "") >= dateFrom]

        if dateTo:
            items = [item for item in items if str(item.get("date") or "") <= dateTo]

        if q:
            terms = [term for term in collapse_ws(q).lower().split(" ") if term]
            if terms:
                items = [
                    item
                    for item in items
                    if all(term in str(item.get("_searchText") or "") for term in terms)
                ]

        if tags:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            if tag_list:
                items = [
                    item for item in items
                    if all(t in (item.get("tags") or []) for t in tag_list)
                ]

        # Compute typeCounts BEFORE recordType filter
        type_counts: dict[str, int] = {}
        for item in items:
            item_type = str(item.get("recordType") or "unknown")
            type_counts[item_type] = type_counts.get(item_type, 0) + 1

        # NOW apply recordType filter
        if recordType not in (None, "", "all"):
            items = [item for item in items if item.get("recordType") == recordType]

        limited = items[: max(1, min(limit, 1000))]

        return {
            "items": [_public_entry_item(item) for item in limited],
            "meta": {
                "totalAvailable": total_available,
                "filtered": len(items),
                "returned": len(limited),
                "sources": [
                    {
                        "id": source["id"],
                        "name": source["name"],
                        "path": source["path"],
                    }
                    for source in sources
                ],
                "typeCounts": type_counts,
            },
        }

    @router.get("/api/entries/detail")
    def entry_detail(key: str, x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
        auth_error = require_admin_token(x_admin_token, admin_token)
        if auth_error is not None:
            return auth_error

        item = _load_entry_detail(key)
        if item is None:
            return error_response(404, "NOT_FOUND", "entry detail not found")
        return {"item": item}

    @router.post("/api/entries/record")
    def entry_record(body: EntryRecordRequest):
        from btwin_core.consolidation import ConsolidationMiddleware

        twin = btwin_factory()
        middleware = ConsolidationMiddleware(
            btwin=twin,
            vector_store=twin.vector_store,
            config=twin.config.consolidation,
        )
        return middleware.process(
            content=body.content,
            topic=body.topic,
            project=body.project_id,
            tags=body.tags,
            subject_projects=body.subject_projects,
            tldr=body.tldr,
            contributors=None,
        )

    @router.post("/api/entries/search")
    def entry_search(body: EntrySearchRequest):
        btwin = btwin_factory()
        filters = {"record_type": body.record_type} if body.record_type else None
        if body.scope == "project" and body.project_id is not None:
            results = btwin.search(
                body.query,
                n_results=body.n_results,
                filters=filters,
                project=body.project_id,
            )
        else:
            results = btwin.search(
                body.query,
                n_results=body.n_results,
                filters=filters,
            )
        return {"results": results}

    @router.post("/api/entries/convo-record")
    def entry_convo_record(body: ConvoRecordRequest):
        result = btwin_factory().record_convo(
            content=body.content,
            requested_by_user=body.requested_by_user,
            topic=body.topic,
            project=body.project_id,
            tags=body.tags,
            subject_projects=body.subject_projects,
            tldr=body.tldr,
        )
        return result

    @router.post("/api/entries/import")
    def entry_import(body: EntryImportRequest):
        result = btwin_factory().import_entry(
            content=body.content,
            date=body.date,
            slug=body.slug,
            tags=body.tags,
            source_path=body.source_path,
            project=body.project_id,
            tldr=body.tldr,
        )
        return result

    @router.post("/api/entries/update")
    def entry_update(body: UpdateEntryRequest):
        return btwin_factory().update_entry(
            record_id=body.record_id,
            content=body.content,
            tags=body.tags,
            subject_projects=body.subject_projects,
            related_records=body.related_records,
            derived_from=body.derived_from,
            contributor=body.contributor,
        )

    @router.get("/api/entries/by-record-id/{record_id}")
    def get_entry_by_record_id(record_id: str):
        result = btwin_factory().get_entry(record_id)
        if result is None:
            return JSONResponse(status_code=404, content={"error": "not_found", "record_id": record_id})
        return result

    return router
