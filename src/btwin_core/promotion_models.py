"""Promotion queue models for reusable knowledge elevation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PromotionStatus = Literal["proposed", "approved", "queued", "promoted"]


class PromotionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    status: PromotionStatus
    proposed_by: str = Field(min_length=1)
    proposed_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    queued_at: datetime | None = None
    promoted_at: datetime | None = None
