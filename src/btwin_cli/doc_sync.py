"""Sync bundled global docs to the user's data directory."""

from __future__ import annotations

import logging
from pathlib import Path

from btwin_cli.resource_paths import resolve_bundled_global_docs_dir

log = logging.getLogger(__name__)


def _default_bundled_dir() -> Path | None:
    """Locate the bundled global/ directory relative to this package."""
    return resolve_bundled_global_docs_dir()


def sync_global_docs(
    data_dir: Path,
    *,
    bundled_dir: Path | None = None,
) -> int:
    """Copy bundled .md and .json docs to data_dir if content has changed."""
    src = bundled_dir or resolve_bundled_global_docs_dir()
    if src is None or not src.is_dir():
        log.debug("No bundled global docs found, skipping sync")
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    updated = 0
    for src_file in sorted(src.iterdir()):
        if not (src_file.name.endswith(".md") or src_file.name.endswith(".json")):
            continue
        dest_file = data_dir / src_file.name
        src_text = src_file.read_text(encoding="utf-8")
        if dest_file.exists() and dest_file.read_text(encoding="utf-8") == src_text:
            continue
        dest_file.write_text(src_text, encoding="utf-8")
        log.info("Synced %s → %s", src_file.name, dest_file)
        updated += 1
    return updated


def sync_global_dirs(
    data_dir: Path,
    *,
    bundled_dir: Path | None = None,
) -> int:
    """Sync bundled subdirectories (e.g., pipelines/) to data_dir."""
    src = bundled_dir or resolve_bundled_global_docs_dir()
    if src is None or not src.is_dir():
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    updated = 0
    for sub in sorted(src.iterdir()):
        if not sub.is_dir():
            continue
        dest_sub = data_dir / sub.name
        dest_sub.mkdir(parents=True, exist_ok=True)
        for src_file in sorted(sub.iterdir()):
            if not src_file.is_file():
                continue
            dest_file = dest_sub / src_file.name
            src_text = src_file.read_text(encoding="utf-8")
            if dest_file.exists() and dest_file.read_text(encoding="utf-8") == src_text:
                continue
            dest_file.write_text(src_text, encoding="utf-8")
            log.info("Synced %s/%s → %s", sub.name, src_file.name, dest_file)
            updated += 1
    return updated
