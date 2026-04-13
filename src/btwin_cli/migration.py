"""Migrate legacy collab records to workflow format."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def scan_collab_data(data_dir: Path) -> list[dict[str, Any]]:
    """Scan for legacy collab entries under entries/{project}/collab/."""
    results = []
    entries_dir = data_dir / "entries"
    if not entries_dir.exists():
        return results
    for project_dir in entries_dir.iterdir():
        if not project_dir.is_dir():
            continue
        collab_dir = project_dir / "collab"
        if not collab_dir.is_dir():
            continue
        for md_file in collab_dir.rglob("*.md"):
            raw = md_file.read_text()
            meta = _parse_frontmatter(raw)
            record_id = meta.get("recordId", meta.get("record_id", ""))
            results.append(
                {
                    "path": str(md_file),
                    "record_id": record_id,
                    "project": project_dir.name,
                    "date": meta.get("date", md_file.parent.name),
                    "metadata": meta,
                }
            )
    return results


def migrate_collab_to_workflow(data_dir: Path, *, dry_run: bool = True) -> dict[str, int]:
    """Migrate collab entries to entries/workflow/{date}/ format."""
    entries = scan_collab_data(data_dir)
    result = {"migrated": 0, "skipped": 0, "errors": 0, "would_migrate": 0}

    for entry in entries:
        workflow_dir = data_dir / "entries" / "workflow" / entry["date"]
        target_file = workflow_dir / f"{entry['record_id']}.md"

        if target_file.exists():
            result["skipped"] += 1
            continue

        if dry_run:
            result["would_migrate"] += 1
            continue

        try:
            source_path = Path(entry["path"])
            raw = source_path.read_text()
            _, body = _split_content(raw)

            meta = entry["metadata"]
            new_meta = _convert_frontmatter(meta, entry["project"])

            workflow_dir.mkdir(parents=True, exist_ok=True)
            new_content = _build_markdown(new_meta, body)
            target_file.write_text(new_content)

            result["migrated"] += 1
        except Exception:
            result["errors"] += 1

    return result


def _parse_frontmatter(raw: str) -> dict[str, Any]:
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}
    return {}


def _split_content(raw: str) -> tuple[dict[str, Any], str]:
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}, parts[2].strip()
    return {}, raw


def _convert_frontmatter(meta: dict[str, Any], project: str) -> dict[str, Any]:
    """Convert collab frontmatter to workflow format."""
    record_id = meta.get("recordId", meta.get("record_id", "unknown"))
    status = meta.get("status", "draft")

    status_map = {"draft": "pending", "handed_off": "active", "completed": "done"}
    wf_status = status_map.get(status, "pending")

    return {
        "record_id": record_id,
        "record_type": "workflow",
        "date": meta.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        "tldr": meta.get("summary", "Migrated from collab record"),
        "source_project": project,
        "created_at": meta.get("createdAt", meta.get("created_at", datetime.now(timezone.utc).isoformat())),
        "last_updated_at": datetime.now(timezone.utc).isoformat(),
        "contributors": [meta.get("authorAgent", meta.get("author_agent", "unknown"))],
        "tags": [f"wf-type:task", f"wf-status:{wf_status}", "migrated-from-collab"],
    }


def _build_markdown(meta: dict[str, Any], body: str) -> str:
    frontmatter = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{frontmatter}---\n\n{body}\n"
