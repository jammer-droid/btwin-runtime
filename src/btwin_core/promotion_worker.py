"""Batch worker for processing promotion queue items."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from btwin_core.indexer import CoreIndexer
from btwin_core.promotion_store import (
    PromotionActorRequiredError,
    PromotionItemNotFoundError,
    PromotionStore,
    PromotionTransitionError,
)
from btwin_core.storage import Storage


@dataclass
class PromotionBatchResult:
    processed: int = 0
    promoted: int = 0
    skipped: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "promoted": self.promoted,
            "skipped": self.skipped,
            "errors": self.errors,
        }


class PromotionWorker:
    def __init__(
        self,
        *,
        storage: Storage,
        promotion_store: PromotionStore,
        indexer: CoreIndexer | None = None,
    ) -> None:
        self.storage = storage
        self.promotion_store = promotion_store
        self.indexer = indexer

    def run_once(self, limit: int | None = None) -> dict[str, int]:
        result = PromotionBatchResult()

        pending_items = self.promotion_store.list_items(
            status="approved"
        ) + self.promotion_store.list_items(status="queued")
        if limit is not None:
            pending_items = pending_items[:limit]

        for item in pending_items:
            result.processed += 1

            source_doc = self.storage.read_orchestration_record_document(item.source_record_id)
            if source_doc is None:
                result.errors += 1
                continue

            if self.storage.promoted_entry_exists(item.item_id):
                try:
                    self.promotion_store.set_status(item.item_id, "promoted", actor="main")
                    result.skipped += 1
                except (PromotionTransitionError, PromotionItemNotFoundError, PromotionActorRequiredError):
                    result.errors += 1
                continue

            if item.status == "approved":
                try:
                    self.promotion_store.set_status(item.item_id, "queued", actor="main")
                except (PromotionTransitionError, PromotionItemNotFoundError, PromotionActorRequiredError):
                    result.errors += 1
                    continue

            saved_path = self.storage.save_promoted_entry(
                item_id=item.item_id,
                source_record_id=item.source_record_id,
                content=str(source_doc.get("content", "")),
            )

            if self.indexer:
                self._index_promoted(saved_path)

            try:
                self.promotion_store.set_status(item.item_id, "promoted", actor="main")
                result.promoted += 1
            except (PromotionTransitionError, PromotionItemNotFoundError, PromotionActorRequiredError):
                result.errors += 1

        return result.as_dict()

    def _index_promoted(self, saved_path: Path) -> None:
        """Mark a promoted entry for indexing and refresh."""
        assert self.indexer is not None
        data_dir = self.indexer.data_dir
        rel = saved_path.relative_to(data_dir).as_posix()
        checksum = self._sha256(saved_path)
        self.indexer.mark_pending(
            doc_id=rel,
            path=rel,
            record_type="promoted",
            checksum=checksum,
        )
        self.indexer.refresh(limit=1)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"sha256:{digest}"
