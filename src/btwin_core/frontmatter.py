"""Standard frontmatter generation for B-TWIN entries."""

from __future__ import annotations

import random
import string
from datetime import datetime, timezone
from typing import Literal

import yaml

RecordType = Literal["convo", "entry", "note", "workflow"]
_VALID_RECORD_TYPES: set[str] = {"convo", "entry", "note", "workflow"}


def _generate_record_id(record_type: str) -> str:
    """Generate unique record_id: {record_type}-{HHMMSSffffff}{3 random chars}."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%H%M%S%f")
    suffix = "".join(random.choices(string.digits, k=3))
    return f"{record_type}-{ts}{suffix}"


def build_frontmatter(
    *,
    record_type: str,
    source_project: str,
    tldr: str,
    tags: list[str] | None = None,
    subject_projects: list[str] | None = None,
    contributors: list[str] | None = None,
) -> dict[str, object]:
    """Build a standard frontmatter dict for a B-TWIN record."""
    if record_type not in _VALID_RECORD_TYPES:
        raise ValueError(
            f"Invalid record_type: {record_type!r}. Must be one of {_VALID_RECORD_TYPES}"
        )

    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()

    fm: dict[str, object] = {
        "record_id": _generate_record_id(record_type),
        "record_type": record_type,
        "date": now.strftime("%Y-%m-%d"),
        "created_at": iso_now,
        "last_updated_at": iso_now,
        "source_project": source_project,
        "tldr": tldr,
        "contributors": list(contributors) if contributors else ["unknown"],
    }

    if tags:
        fm["tags"] = list(tags)
    if subject_projects:
        fm["subject_projects"] = list(subject_projects)

    return fm


def parse_frontmatter_to_metadata(raw_content: str) -> dict[str, str]:
    """Parse markdown frontmatter and return ChromaDB-compatible metadata dict."""
    if not raw_content.startswith("---\n"):
        return {}

    parts = raw_content.split("---\n", 2)
    if len(parts) < 3:
        return {}

    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}

    if not isinstance(fm, dict):
        return {}

    meta: dict[str, str] = {}

    for key in ("record_id", "date", "source_project", "derived_from", "tldr"):
        if key in fm and fm[key]:
            meta[key] = str(fm[key])

    if "record_type" in fm:
        meta["record_type"] = str(fm["record_type"])

    for key in ("tags", "subject_projects", "related_records", "contributors"):
        if key in fm and fm[key]:
            values = fm[key] if isinstance(fm[key], list) else [fm[key]]
            meta[key] = ",".join(str(v) for v in values)

    return meta
