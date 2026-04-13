"""Handoff snapshot and archive helpers."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


HANDOFF_GITIGNORE_ENTRY = "HANDOFF.md"
HANDOFF_SNAPSHOT_FILENAME = "HANDOFF.md"


@dataclass(frozen=True)
class HandoffWriteResult:
    snapshot_path: Path
    archive_path: Path
    gitignore_path: Path
    archive_row: dict[str, Any]


def write_handoff_record(
    project_root: Path,
    *,
    record_id: str,
    summary: str,
    dispatch: str,
    branch: str | None = None,
    commit: str | None = None,
    tags: Iterable[str] = (),
    background: str | None = None,
    intent: str | None = None,
    current_state: str | None = None,
    verification: str | None = None,
    risks: str | None = None,
    next_steps: str | None = None,
    starter_context: str | None = None,
) -> HandoffWriteResult:
    """Write the latest snapshot and append a global archive row."""
    project_root = project_root.expanduser()
    project_root.mkdir(parents=True, exist_ok=True)
    tags_list = tuple(tags)
    resolved_project_root = project_root.resolve()
    git_remote = _git_remote_url(project_root)
    project_key = _project_key(project_root, git_remote=git_remote)

    snapshot = build_handoff_snapshot(
        record_id=record_id,
        summary=summary,
        dispatch=dispatch,
        branch=branch,
        commit=commit,
        tags=tags_list,
        background=background,
        intent=intent,
        current_state=current_state,
        verification=verification,
        risks=risks,
        next_steps=next_steps,
        starter_context=starter_context,
    )
    snapshot_path = write_latest_snapshot(project_root, snapshot)
    gitignore_path = project_root / ".gitignore"
    if not _is_git_tracked(project_root, HANDOFF_SNAPSHOT_FILENAME):
        gitignore_path = ensure_gitignore_entry(project_root, HANDOFF_GITIGNORE_ENTRY)
    archive_row = build_archive_row(
        record_id=record_id,
        summary=summary,
        dispatch=dispatch,
        branch=branch,
        commit=commit,
        tags=tags_list,
        background=background,
        intent=intent,
        current_state=current_state,
        verification=verification,
        risks=risks,
        next_steps=next_steps,
        starter_context=starter_context,
        project_root=resolved_project_root,
        project_key=project_key,
        git_remote=git_remote,
    )
    archive_path = append_archive_row(project_root, archive_row, git_remote=git_remote)
    return HandoffWriteResult(
        snapshot_path=snapshot_path,
        archive_path=archive_path,
        gitignore_path=gitignore_path,
        archive_row=archive_row,
    )


def build_handoff_snapshot(
    *,
    record_id: str,
    summary: str,
    dispatch: str,
    branch: str | None = None,
    commit: str | None = None,
    tags: Iterable[str] = (),
    background: str | None = None,
    intent: str | None = None,
    current_state: str | None = None,
    verification: str | None = None,
    risks: str | None = None,
    next_steps: str | None = None,
    starter_context: str | None = None,
) -> str:
    """Build the latest handoff snapshot markdown."""
    updated = datetime.now(timezone.utc).date().isoformat()
    lines: list[str] = [
        "# Current Handoff",
        "",
        f"- **Updated**: {updated}",
        f"- **Record**: {record_id}",
        f"- **Summary**: {summary}",
        f"- **Dispatch**: {dispatch}",
    ]
    if branch:
        lines.append(f"- **Branch**: {branch}")
    if commit:
        lines.append(f"- **Commit**: {commit}")
    tags_list = [tag for tag in tags if tag]
    if tags_list:
        lines.append(f"- **Tags**: {', '.join(tags_list)}")

    sections = [
        ("Background", background),
        ("Intent and Decisions", intent),
        ("Current State", current_state),
        ("Verification", verification),
        ("Risks and Open Questions", risks),
        ("Next Steps", next_steps),
        ("Starter Context", starter_context),
    ]
    for title, body in sections:
        if not body:
            continue
        lines.extend(["", f"## {title}", "", body.strip()])

    lines.append("")
    return "\n".join(lines)


def build_archive_row(
    *,
    record_id: str,
    summary: str,
    dispatch: str,
    branch: str | None = None,
    commit: str | None = None,
    tags: Iterable[str] = (),
    background: str | None = None,
    intent: str | None = None,
    current_state: str | None = None,
    verification: str | None = None,
    risks: str | None = None,
    next_steps: str | None = None,
    starter_context: str | None = None,
    project_root: Path,
    project_key: str,
    git_remote: str | None,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "record_id": record_id,
        "summary": summary,
        "dispatch": dispatch,
        "branch": branch,
        "commit": commit,
        "tags": [tag for tag in tags if tag],
        "background": background,
        "intent": intent,
        "current_state": current_state,
        "verification": verification,
        "risks": risks,
        "next_steps": next_steps,
        "starter_context": starter_context,
        "project_root": str(project_root),
        "project_key": project_key,
        "git_remote": git_remote,
    }


def write_latest_snapshot(project_root: Path, content: str) -> Path:
    snapshot_path = project_root / HANDOFF_SNAPSHOT_FILENAME
    _atomic_write_text(snapshot_path, content)
    return snapshot_path


def append_archive_row(project_root: Path, row: dict[str, Any], *, git_remote: str | None = None) -> Path:
    archive_path = _global_archive_path(project_root, git_remote=git_remote)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return archive_path


def _global_archive_path(project_root: Path, *, git_remote: str | None = None) -> Path:
    return _handoff_archive_root() / _project_key(project_root, git_remote=git_remote) / "handoffs.jsonl"


def _project_key(project_root: Path, git_remote: str | None = None) -> str:
    git_remote = git_remote or _git_remote_url(project_root)
    if git_remote:
        repo_name = _repo_name_from_git_remote(git_remote)
        if repo_name:
            return repo_name
    return _project_key_from_path(project_root)


def _project_key_from_path(project_root: Path) -> str:
    canonical_path = project_root.resolve().as_posix().strip("/")
    return f"path-{canonical_path.replace('/', '__').replace(':', '_')}"


def _handoff_archive_root() -> Path:
    return Path.home() / ".btwin" / "projects"


def _repo_name_from_git_remote(remote_url: str) -> str | None:
    tail = remote_url.rstrip("/").rsplit("/", 1)[-1]
    tail = tail.rsplit(":", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return tail or None


def _git_remote_url(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    url = result.stdout.strip()
    if not url:
        return None
    return _normalize_git_remote_url(url)


def _is_git_tracked(project_root: Path, relative_path: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "ls-files", "--error-unmatch", relative_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _normalize_git_remote_url(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized


def ensure_gitignore_entry(project_root: Path, entry: str) -> Path:
    gitignore_path = project_root / ".gitignore"
    if gitignore_path.exists():
        lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    cleaned: list[str] = []
    seen = False
    for line in lines:
        if line == entry:
            if not seen:
                cleaned.append(line)
                seen = True
            continue
        cleaned.append(line)

    if not seen:
        cleaned.append(entry)

    _atomic_write_text(gitignore_path, "\n".join(cleaned).rstrip("\n") + "\n")
    return gitignore_path


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)
