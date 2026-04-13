"""Indexer status and manifest entry models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IndexStatus = Literal["pending", "indexed", "stale", "failed", "deleted"]
RecordType = Literal["entry", "convo", "collab", "promoted", "workflow"]


class IndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    record_type: RecordType
    checksum: str = Field(min_length=1)
    status: IndexStatus
    project: str | None = None
    doc_version: int = Field(ge=1)
    error: str | None = None
    pending_since: float | None = None
