"""YAML-backed promotion queue store."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

import yaml

from btwin_core.promotion_models import PromotionItem, PromotionStatus

_ALLOWED_TRANSITIONS: MappingProxyType[PromotionStatus, frozenset[PromotionStatus]] = MappingProxyType(
    {
        "proposed": frozenset({"approved"}),
        "approved": frozenset({"queued"}),
        "queued": frozenset({"promoted"}),
        "promoted": frozenset(),
    }
)


class PromotionStoreError(Exception):
    """Base exception for promotion store operations."""


class PromotionItemNotFoundError(PromotionStoreError):
    """Raised when requested queue item id is not found."""


class PromotionTransitionError(PromotionStoreError):
    """Raised when an invalid status transition is requested."""


class PromotionActorRequiredError(PromotionStoreError):
    """Raised when approval transition is requested without actor."""


class PromotionStore:
    def __init__(self, queue_path: Path) -> None:
        self.queue_path = queue_path
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self._items: list[PromotionItem] = self._load_items()

    def list_items(self, status: PromotionStatus | None = None) -> list[PromotionItem]:
        source = self._items if status is None else [item for item in self._items if item.status == status]
        return [item.model_copy(deep=True) for item in source]

    def enqueue(self, source_record_id: str, proposed_by: str) -> PromotionItem:
        now = datetime.now(timezone.utc)
        item = PromotionItem(
            item_id=f"prm_{uuid4().hex[:12]}",
            source_record_id=source_record_id,
            status="proposed",
            proposed_by=proposed_by,
            proposed_at=now,
        )
        self._items.append(item)
        self._save_items()
        return item.model_copy(deep=True)

    def set_status(self, item_id: str, to_status: PromotionStatus, actor: str | None = None) -> PromotionItem:
        idx = self._find_item_index(item_id)
        item = self._items[idx]

        if to_status not in _ALLOWED_TRANSITIONS[item.status]:
            raise PromotionTransitionError(f"cannot transition from {item.status} to {to_status}")

        if to_status == "approved" and not actor:
            raise PromotionActorRequiredError("actor is required for approved transition")

        now = datetime.now(timezone.utc)
        updates: dict[str, object] = {"status": to_status}
        if to_status == "approved":
            updates["approved_by"] = actor
            updates["approved_at"] = now
        elif to_status == "queued":
            updates["queued_at"] = now
        elif to_status == "promoted":
            updates["promoted_at"] = now

        updated = item.model_copy(update=updates)
        self._items[idx] = updated
        self._save_items()
        return updated.model_copy(deep=True)

    def _find_item_index(self, item_id: str) -> int:
        for idx, item in enumerate(self._items):
            if item.item_id == item_id:
                return idx
        raise PromotionItemNotFoundError(f"promotion item not found: {item_id}")

    def _load_items(self) -> list[PromotionItem]:
        if not self.queue_path.exists():
            return []

        raw = yaml.safe_load(self.queue_path.read_text()) or []
        if not isinstance(raw, list):
            raise PromotionStoreError("promotion queue file must contain a list")
        return [PromotionItem.model_validate(item) for item in raw]

    def _save_items(self) -> None:
        serialized = [item.model_dump(mode="json") for item in self._items]
        payload = yaml.dump(serialized, allow_unicode=True, sort_keys=False)

        tmp_path = self.queue_path.with_suffix(self.queue_path.suffix + ".tmp")
        tmp_path.write_text(payload)
        tmp_path.replace(self.queue_path)
