"""Wheel-safe resource path helpers owned by the CLI package."""

from __future__ import annotations

from pathlib import Path

from btwin_core.resource_paths import (
    resolve_bundled_protocols_dir as _resolve_bundled_protocols_dir,
    resolve_bundled_providers_path as _resolve_bundled_providers_path,
    resolve_workspace_root,
)


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def _package_dir(name: str) -> Path | None:
    candidate = _package_root() / name
    if candidate.is_dir():
        return candidate
    return None


def resolve_bundled_global_docs_dir(workspace_root: Path | None = None) -> Path | None:
    """Return the package-owned bundled global docs directory."""
    bundled = _package_dir("global")
    if bundled is not None:
        return bundled

    from btwin_core.resource_paths import resolve_bundled_global_docs_dir as _fallback

    return _fallback(workspace_root)


def resolve_bundled_skills_dir(workspace_root: Path | None = None) -> Path | None:
    """Return the package-owned bundled skills directory."""
    bundled = _package_dir("skills")
    if bundled is not None:
        return bundled

    from btwin_core.resource_paths import resolve_bundled_skills_dir as _fallback

    return _fallback(workspace_root)


def resolve_bundled_protocols_dir() -> Path | None:
    """Return the bundled protocols directory if it exists in the core package."""
    return _resolve_bundled_protocols_dir()


def resolve_bundled_providers_path() -> Path | None:
    """Return the bundled providers manifest if it exists in the core package."""
    return _resolve_bundled_providers_path()
