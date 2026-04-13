"""Shared metadata models for common foundation records."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommonRecordMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    doc_version: int = Field(alias="docVersion", ge=1)
    status: str = Field(min_length=1)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    record_type: str = Field(alias="recordType", min_length=1)

    @field_validator("status", "record_type")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text fields must not be empty")
        return cleaned

    @field_validator("created_at", "updated_at")
    @classmethod
    def _require_timezone_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value
