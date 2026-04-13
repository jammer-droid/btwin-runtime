"""Consolidation middleware: merges similar records at write time."""

from __future__ import annotations

import logging
from typing import Any

from btwin_core.config import ConsolidationConfig

logger = logging.getLogger(__name__)


def cosine_distance_to_similarity(distance: float | None) -> float:
    """Convert ChromaDB cosine distance [0, 2] to similarity [1, 0].

    ``None`` is treated as zero similarity (safe default: create new).
    """
    if distance is None:
        return 0.0
    clamped = max(0.0, min(2.0, distance))
    return 1.0 - (clamped / 2.0)


def _merge_content(existing: str, incoming: str) -> str:
    existing = (existing or "").rstrip()
    incoming = (incoming or "").lstrip()
    if not existing:
        return incoming
    if not incoming:
        return existing
    return f"{existing}\n\n---\n\n{incoming}"


def _merge_list(
    existing: list[str] | None,
    incoming: list[str] | None,
) -> list[str] | None:
    if existing is None and incoming is None:
        return None
    merged: list[str] = []
    for value in (existing or []) + (incoming or []):
        if value not in merged:
            merged.append(value)
    return merged or None


class ConsolidationMiddleware:
    """Wraps ``BTwin.record()`` with similarity-based consolidation.

    When a caller wants to save a new record, this middleware first
    searches for similar existing records using pure vector similarity.

    - If the best match is above ``auto_threshold``, the existing record
      is updated in place via ``BTwin.update_entry()``.
    - If the best match is between ``suggest_threshold`` and
      ``auto_threshold``, a new record is created and candidate matches
      are returned for the caller to inspect.
    - Otherwise, a new record is created without candidates.

    When ``config.enabled`` is False, the middleware is a pass-through
    that simply calls ``BTwin.record()``.
    """

    def __init__(
        self,
        *,
        btwin: Any,
        vector_store: Any,
        config: ConsolidationConfig,
    ) -> None:
        self._btwin = btwin
        self._vector_store = vector_store
        self._config = config

    def process(
        self,
        *,
        content: str,
        tldr: str,
        topic: str | None = None,
        tags: list[str] | None = None,
        subject_projects: list[str] | None = None,
        contributors: list[str] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        if not self._config.enabled:
            return self._create_new(
                content=content,
                tldr=tldr,
                topic=topic,
                tags=tags,
                subject_projects=subject_projects,
                contributors=contributors,
                project=project,
            )

        candidates = self._find_similar(tldr=tldr, project=project)
        best = self._best_match(candidates)

        if best is not None and best["_similarity"] >= self._config.auto_threshold:
            consolidated = self._consolidate(
                best=best,
                content=content,
                tldr=tldr,
                tags=tags,
                subject_projects=subject_projects,
                contributor=(contributors or [None])[0],
            )
            if consolidated is not None:
                return consolidated
            # Consolidation failed → fall through to create with the
            # candidate surfaced as a suggestion.

        suggestions: list[dict[str, Any]] | None = None
        if best is not None and best["_similarity"] >= self._config.suggest_threshold:
            suggestions = self._format_candidates(candidates)

        return self._create_new(
            content=content,
            tldr=tldr,
            topic=topic,
            tags=tags,
            subject_projects=subject_projects,
            contributors=contributors,
            project=project,
            similar_candidates=suggestions,
        )

    def _find_similar(
        self,
        *,
        tldr: str,
        project: str | None,
    ) -> list[dict[str, Any]]:
        filters: dict[str, str] = {"record_type": "entry"}
        if project:
            filters["source_project"] = project

        try:
            return self._vector_store.search(
                query=tldr,
                n_results=self._config.search_candidates,
                metadata_filters=filters,
                hybrid=False,
                recency_half_life_days=0,
                mmr_lambda=1.0,
            )
        except Exception:
            logger.warning(
                "Consolidation similarity search failed; falling back to create",
                exc_info=True,
            )
            return []

    def _best_match(
        self,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not candidates:
            return None
        best = None
        best_score = -1.0
        for candidate in candidates:
            score = cosine_distance_to_similarity(candidate.get("distance"))
            if score > best_score:
                best = {**candidate, "_similarity": score}
                best_score = score
        return best

    def _format_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for candidate in candidates:
            score = cosine_distance_to_similarity(candidate.get("distance"))
            if score < self._config.suggest_threshold:
                continue
            metadata = candidate.get("metadata") or {}
            formatted.append(
                {
                    "record_id": metadata.get("record_id"),
                    "score": round(float(score), 4),
                    "tldr": candidate.get("content"),
                    "path": candidate.get("id"),
                }
            )
        return formatted

    def _create_new(
        self,
        *,
        content: str,
        tldr: str,
        topic: str | None,
        tags: list[str] | None,
        subject_projects: list[str] | None,
        contributors: list[str] | None,
        project: str | None,
        similar_candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result = self._btwin.record(
            content=content,
            topic=topic,
            tags=tags,
            subject_projects=subject_projects,
            tldr=tldr,
            contributors=contributors,
            project=project,
        )
        response: dict[str, Any] = {
            "action": "created",
            "date": result.get("date"),
            "slug": result.get("slug"),
            "path": result.get("path"),
        }
        if similar_candidates:
            response["similar_candidates"] = similar_candidates
        return response

    def _consolidate(
        self,
        *,
        best: dict[str, Any],
        content: str,
        tldr: str,
        tags: list[str] | None,
        subject_projects: list[str] | None,
        contributor: str | None,
    ) -> dict[str, Any] | None:
        candidate_meta = best.get("metadata") or {}
        record_id = candidate_meta.get("record_id")
        if not record_id:
            logger.warning(
                "Consolidation candidate is missing record_id; falling back",
            )
            return None

        existing = self._btwin.get_entry(record_id)
        if existing is None:
            logger.warning(
                "Consolidation candidate %s not found on disk; falling back",
                record_id,
            )
            return None

        existing_fm = existing.get("frontmatter") or {}
        merged_content = _merge_content(existing.get("content", ""), content)
        merged_tags = _merge_list(existing_fm.get("tags"), tags)
        merged_projects = _merge_list(existing_fm.get("subject_projects"), subject_projects)

        try:
            update_result = self._btwin.update_entry(
                record_id=record_id,
                content=merged_content,
                tags=merged_tags,
                subject_projects=merged_projects,
                contributor=contributor,
                tldr=tldr,
            )
        except Exception:
            logger.warning(
                "update_entry failed during consolidation of %s",
                record_id,
                exc_info=True,
            )
            return None

        if not update_result or not update_result.get("ok"):
            return None

        return {
            "action": "consolidated",
            "record_id": record_id,
            "matched_score": round(float(best["_similarity"]), 4),
            "date": existing_fm.get("date"),
            "slug": existing_fm.get("slug"),
            "path": update_result.get("path"),
        }
