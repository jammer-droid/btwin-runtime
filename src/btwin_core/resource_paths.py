"""Locate bundled resources for the package-canonical core layout."""

from __future__ import annotations

import os
from pathlib import Path


def _discover_workspace_root(start: Path | None = None) -> Path:
    """Discover the workspace root used for bundled resource lookup."""
    if start is not None:
        return start

    env_root = os.environ.get("BTWIN_WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).expanduser()

    here = Path(__file__).resolve()
    pyproject_candidates = [candidate for candidate in here.parents if (candidate / "pyproject.toml").exists()]
    for candidate in reversed(pyproject_candidates):
        if (candidate / "packages").is_dir():
            return candidate
    for candidate in reversed(pyproject_candidates):
        if (candidate / "global").is_dir():
            return candidate
    for candidate in reversed(pyproject_candidates):
        if (candidate / "src" / "btwin" / "skills").is_dir():
            return candidate
    if pyproject_candidates:
        return pyproject_candidates[-1]
    return here.parents[3]


def _first_existing_dir(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def resolve_workspace_root(start: Path | None = None) -> Path:
    """Resolve the active workspace root without assuming a monorepo layout."""
    if start is not None:
        return start

    env_root = os.environ.get("BTWIN_WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).expanduser()

    return Path.cwd()


def resolve_bundled_providers_path() -> Path | None:
    """Return the bundled providers manifest if it exists in the package."""
    candidate = Path(__file__).resolve().parent / "global" / "providers.json"
    if candidate.exists():
        return candidate
    return None


def resolve_bundled_protocols_dir() -> Path | None:
    """Return the bundled protocols directory if it exists in the package."""
    candidate = Path(__file__).resolve().parent / "global" / "protocols"
    if candidate.is_dir():
        return candidate
    return None


def resolve_bundled_global_docs_dir(workspace_root: Path | None = None) -> Path | None:
    """Return the bundled global-docs directory for the active layout."""
    root = _discover_workspace_root(workspace_root)
    candidates = [
        root / "packages" / "btwin-cli" / "src" / "btwin_cli" / "global",
        root / "packages" / "btwin-core" / "src" / "btwin_core" / "global",
        root / "global",
    ]
    return _first_existing_dir(candidates)


def resolve_bundled_skills_dir(workspace_root: Path | None = None) -> Path | None:
    """Return the bundled skills directory for the active layout."""
    root = _discover_workspace_root(workspace_root)
    candidates = [
        root / "packages" / "btwin-cli" / "src" / "btwin_cli" / "skills",
        root / "src" / "btwin" / "skills",
    ]
    return _first_existing_dir(candidates)
