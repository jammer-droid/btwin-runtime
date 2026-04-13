"""Entry frontmatter validator and fixer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


REQUIRED_FIELDS = ("record_id", "record_type", "date", "created_at", "last_updated_at", "source_project", "tldr")
LEGACY_FIELDS = {"slug", "recordType", "requestedByUser", "project", "topic"}


@dataclass
class ValidationResult:
    path: Path
    valid: bool
    issues: list[str] = field(default_factory=list)


def _parse_frontmatter(path: Path) -> tuple[dict | None, str]:
    """Parse frontmatter and body from a markdown file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None, text
    if not isinstance(fm, dict):
        return None, text
    return fm, parts[2]


def validate_entry(path: Path) -> ValidationResult:
    """Validate a single entry file against the canonical schema."""
    fm, _ = _parse_frontmatter(path)
    if fm is None:
        return ValidationResult(path=path, valid=False, issues=["no valid frontmatter"])

    issues: list[str] = []

    for req in REQUIRED_FIELDS:
        if req not in fm:
            issues.append(f"missing required: {req}")
        elif req == "tldr" and not fm[req]:
            issues.append("empty tldr")

    if "contributors" not in fm:
        issues.append("missing: contributors")
    elif isinstance(fm["contributors"], list) and len(fm["contributors"]) == 0:
        issues.append("empty contributors")

    for key in fm:
        if key in LEGACY_FIELDS:
            issues.append(f"legacy field: {key}")

    return ValidationResult(path=path, valid=len(issues) == 0, issues=issues)


def fix_entry(path: Path) -> bool:
    """Fix known issues in an entry file. Returns True if changes were made."""
    fm, body = _parse_frontmatter(path)
    if fm is None:
        return False

    changed = False

    for key in list(fm.keys()):
        if key in LEGACY_FIELDS:
            del fm[key]
            changed = True

    if isinstance(fm.get("contributors"), list) and len(fm["contributors"]) == 0:
        fm["contributors"] = ["unknown"]
        changed = True

    if not changed:
        return False

    frontmatter_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{frontmatter_str}\n---\n{body}", encoding="utf-8")
    return True
