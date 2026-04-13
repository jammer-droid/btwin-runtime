"""B-TWIN MCP Proxy -- lightweight MCP server that forwards to HTTP API.

Instead of importing heavy dependencies (chromadb, indexer, storage),
this proxy forwards MCP tool calls as HTTP requests to a running
B-TWIN API server, automatically injecting the projectId parameter.

Architecture:
    LLM Client -> MCP Proxy (project="myproj", backend="http://localhost:8787")
                     |
               HTTP POST /api/entries/record  {"content": "...", "projectId": "myproj"}
                     |
               B-TWIN HTTP API (serve-api)
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from btwin_core.config import resolve_data_dir
from btwin_cli.instructions import build_instructions

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger(__name__)

# Module-level state, set by main() before mcp.run().
_project: str = ""
_backend: str = "http://localhost:8787"
_client: httpx.Client | None = None
_data_dir: Path | None = None

mcp = FastMCP("btwin")


def _active_data_dir() -> Path:
    """Return the active btwin data directory used by the proxy."""
    return _data_dir or resolve_data_dir()


def configure_runtime(data_dir: Path | None = None) -> None:
    """Bind proxy instructions to the active data directory."""
    global _data_dir
    if data_dir is not None:
        _data_dir = data_dir
    mcp._mcp_server.instructions = build_instructions(_active_data_dir())


def _http() -> httpx.Client:
    """Lazy-initialise and return the shared httpx client."""
    global _client
    if _client is None:
        _client = httpx.Client(base_url=_backend, timeout=30.0)
    return _client


def _post(path: str, data: dict) -> dict:
    """POST JSON to the backend API and return parsed response."""
    resp = _http().post(path, json=data)
    resp.raise_for_status()
    return resp.json()


def _get(path: str, params: dict | None = None) -> dict:
    """GET from the backend API and return parsed response."""
    resp = _http().get(path, params=params)
    resp.raise_for_status()
    return resp.json()


def _inject_project(data: dict) -> None:
    """Inject projectId into *data* in-place (if configured)."""
    if _project:
        data["projectId"] = _project


# ---------------------------------------------------------------------------
# MCP Tools -- same names & signatures as server.py, forwarded via HTTP
# ---------------------------------------------------------------------------


@mcp.tool()
def btwin_get_guidelines() -> str:
    """Get btwin usage guidelines that all LLM agents must follow.

    Call this at the start of a session to understand how to use btwin properly.
    Returns rules for recording, searching, and session management.
    """
    guidelines_path = _active_data_dir() / "guidelines.md"
    if guidelines_path.exists():
        return guidelines_path.read_text()
    return f"No guidelines file found. Create one at {guidelines_path}"


@mcp.tool()
def btwin_record(
    content: str,
    tldr: str,
    topic: str | None = None,
    tags: str | None = None,
    subject_projects: str | None = None,
) -> str:
    """Manually record a note or thought.

    Saves the content as a markdown entry and indexes it for future search.
    When a highly similar entry already exists, the existing entry is
    updated in place instead of creating a duplicate.

    Args:
        content: The text content to record
        tldr: Required 1-3 sentence summary for search indexing
        topic: Optional topic slug (e.g., "career-ta", "unreal-study")
        tags: Optional comma-separated tags (e.g., "roadmap,planning")
        subject_projects: Optional comma-separated project names this record is about
    """
    import json

    data: dict = {"content": content, "tldr": tldr}
    if topic:
        data["topic"] = topic
    if tags:
        data["tags"] = [t.strip() for t in tags.split(",")]
    if subject_projects:
        data["subjectProjects"] = [p.strip() for p in subject_projects.split(",")]
    _inject_project(data)
    result = _post("/api/entries/record", data)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def btwin_search(
    query: str,
    n_results: int = 5,
    record_type: str | None = None,
    scope: str = "project",
) -> str:
    """Search past entries by semantic similarity.

    Returns relevant past records that match the query.

    Args:
        query: The search query
        n_results: Maximum number of results to return (default: 5)
        record_type: Optional metadata filter (e.g. convo, orchestration, entry)
        scope: "project" (default) searches current project only, "all" searches everything
    """
    data: dict = {"query": query, "nResults": n_results, "scope": scope}
    if record_type:
        data["recordType"] = record_type

    if scope != "all":
        # Only inject projectId for project-scoped searches
        _inject_project(data)

    result = _post("/api/entries/search", data)

    if not result:
        return "No matching records found."
    if isinstance(result, list):
        lines: list[str] = []
        for r in result:
            meta = r.get("metadata", {})
            record_id = meta.get("record_id", "unknown")
            date = meta.get("date", "")
            tags_str = meta.get("tags", "")
            project = meta.get("source_project", "")
            lines.append(f"### {record_id} ({date})")
            lines.append(f"TLDR: {r.get('content', '')}")
            if tags_str:
                lines.append(f"Tags: {tags_str}")
            if project:
                lines.append(f"Project: {project}")
            lines.append(f"→ Full content: btwin://record/{record_id}")
            lines.append("")
        return "\n".join(lines) if lines else "No matching records found."
    return str(result)


@mcp.tool()
def btwin_convo_record(
    content: str,
    tldr: str,
    requested_by_user: bool = False,
    tags: str | None = None,
    subject_projects: str | None = None,
) -> str:
    """Record user conversation memory.

    Args:
        content: Conversation memory content to store
        tldr: Required 1-3 sentence summary for search indexing
        requested_by_user: Whether this was an explicit user remember request
        tags: Optional comma-separated tags (e.g., "meeting,career")
        subject_projects: Optional comma-separated project names this record is about
    """
    data: dict = {"content": content, "tldr": tldr, "requestedByUser": requested_by_user}
    if tags:
        data["tags"] = [t.strip() for t in tags.split(",")]
    if subject_projects:
        data["subjectProjects"] = [p.strip() for p in subject_projects.split(",")]
    _inject_project(data)
    result = _post("/api/entries/convo-record", data)
    return f"Convo recorded: {result.get('path', 'ok')}"


@mcp.tool()
def btwin_import_entry(
    content: str,
    tldr: str,
    date: str,
    slug: str,
    tags: str | None = None,
    source_path: str | None = None,
) -> str:
    """Import a single entry with explicit date, slug, and tags.

    Args:
        content: The markdown content of the entry
        tldr: Required 1-3 sentence summary for search indexing
        date: Date in YYYY-MM-DD format (e.g., "2026-02-24")
        slug: Filename slug (e.g., "sprint-review")
        tags: Comma-separated tags (e.g., "backend,api-design,review")
        source_path: Original file path for dedup tracking
    """
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    data: dict = {"content": content, "tldr": tldr, "date": date, "slug": slug}
    if tag_list:
        data["tags"] = tag_list
    if source_path:
        data["sourcePath"] = source_path
    _inject_project(data)
    result = _post("/api/entries/import", data)
    return f"Imported: {result.get('date', date)}/{result.get('slug', slug)} -> {result.get('path', 'ok')}"


@mcp.tool()
def btwin_update_entry(
    record_id: str,
    content: str | None = None,
    tags: str | None = None,
    subject_projects: str | None = None,
    related_records: str | None = None,
    derived_from: str | None = None,
    contributor: str | None = None,
) -> str:
    """Update an existing entry by record_id. Updates metadata, content, or relationships.

    Args:
        record_id: The record_id of the entry to update
        content: New content to replace the body (optional)
        tags: Comma-separated tags to set (optional)
        subject_projects: Comma-separated project names (optional)
        related_records: Comma-separated related record IDs (optional)
        derived_from: Record ID this entry is derived from (optional)
        contributor: Contributor identifier to add (optional)
    """
    data: dict = {"recordId": record_id}
    if content is not None:
        data["content"] = content
    if tags is not None:
        data["tags"] = [t.strip() for t in tags.split(",")]
    if subject_projects is not None:
        data["subjectProjects"] = [p.strip() for p in subject_projects.split(",")]
    if related_records is not None:
        data["relatedRecords"] = [r.strip() for r in related_records.split(",")]
    if derived_from is not None:
        data["derivedFrom"] = derived_from
    if contributor is not None:
        data["contributor"] = contributor

    result = _post("/api/entries/update", data)
    if result.get("ok"):
        return f"Updated: {result.get('record_id', record_id)}"
    return f"Update failed: {result.get('error', 'unknown')}"


@mcp.tool()
def btwin_start_session(topic: str | None = None) -> str:
    """Start a new conversation session.

    Args:
        topic: Optional topic slug (e.g., "unreal-shader-study", "career-ta")
    """
    data: dict = {}
    if topic:
        data["topic"] = topic
    # NOTE: SessionStartRequest has extra="forbid" and no projectId field,
    # so we must NOT inject projectId here.
    result = _post("/api/sessions/start", data)
    return f"Session started: {result.get('topic') or 'untitled'}"


@mcp.tool()
def btwin_end_session(
    summary: str,
    tldr: str,
    slug: str | None = None,
    tags: str | None = None,
    subject_projects: str | None = None,
) -> str:
    """End the current session and save it as a searchable entry.

    Args:
        summary: A summary of the conversation
        tldr: Required 1-3 sentence summary for search indexing
        slug: Optional filename slug
        tags: Optional comma-separated tags (e.g., "planning,roadmap")
        subject_projects: Optional comma-separated project names this record is about
    """
    data: dict = {"summary": summary, "tldr": tldr}
    if slug:
        data["slug"] = slug
    if tags:
        data["tags"] = [t.strip() for t in tags.split(",")]
    if subject_projects:
        data["subjectProjects"] = [p.strip() for p in subject_projects.split(",")]
    _inject_project(data)
    result = _post("/api/sessions/end", data)
    if result is None:
        return "No active session to end."
    return f"Session saved: {result.get('date', '?')}/{result.get('slug', '?')}"


@mcp.tool()
def btwin_session_status() -> str:
    """Check the current session status."""
    result = _get("/api/sessions/status")
    if not result.get("active"):
        return "No active session."
    return (
        f"Active session: {result.get('topic') or 'untitled'}\n"
        f"Messages: {result.get('message_count', 0)}\n"
        f"Started: {result.get('created_at', '?')}"
    )


# ---------------------------------------------------------------------------
# Thread / Protocol Collaboration Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def btwin_protocol_list() -> str:
    """List available collaboration protocols.

    Returns protocol names and descriptions.
    """
    import json
    result = _get("/api/protocols")
    if not result:
        return "No protocols found."
    lines = []
    for p in result:
        lines.append(f"- **{p['name']}**: {p.get('description', '')}")
    return "\n".join(lines)


@mcp.tool()
def btwin_protocol_get(name: str) -> str:
    """Get full protocol definition by name.

    Returns protocol phases, templates, and collaboration rules.
    Agents call this to understand the expected contribution structure.

    Args:
        name: Protocol name (e.g. 'debate', 'code-review')
    """
    import json
    try:
        result = _get(f"/api/protocols/{name}")
        return json.dumps(result, ensure_ascii=False, indent=2)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Protocol '{name}' not found."
        raise


@mcp.tool()
def btwin_thread_create(topic: str, protocol: str, participants: list[str] | None = None) -> str:
    """Create a new collaboration thread.

    Args:
        topic: Thread topic description
        protocol: Protocol name to use (e.g. 'debate', 'code-review')
        participants: Optional list of agent names to add as initial participants
    """
    import json
    data: dict = {"topic": topic, "protocol": protocol}
    if participants:
        data["participants"] = participants
    result = _post("/api/threads", data)
    return (
        f"Thread created: {result['thread_id']}\n"
        f"Topic: {result['topic']}\n"
        f"Protocol: {result['protocol']}\n"
        f"Participants: {[p['name'] for p in result.get('participants', [])]}"
    )


@mcp.tool()
def btwin_thread_join(thread_id: str, agent_name: str | None = None) -> str:
    """Join an existing thread as a participant.

    Returns thread metadata and protocol definition only.
    Use btwin_thread_read to fetch contributions and messages.

    Args:
        thread_id: Thread ID to join
        agent_name: Your agent name (auto-detected from project if omitted)
    """
    import json
    name = agent_name or _project or "unknown"
    try:
        thread = _post(f"/api/threads/{thread_id}/join", {"agentName": name})
        proto = None
        try:
            proto = _get(f"/api/protocols/{thread['protocol']}")
        except Exception:
            pass
        result = {"thread": thread, "protocol": proto}
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Thread '{thread_id}' not found."
        raise


@mcp.tool()
def btwin_thread_context(thread_id: str) -> str:
    """Get current phase context including template, guidance, previous phase contributions, and phase participants."""
    import json
    try:
        result = _get(f"/api/threads/{thread_id}/phase-context")
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Thread '{thread_id}' not found."
        raise


@mcp.tool()
def btwin_thread_contribute(thread_id: str, phase: str, content: str, tldr: str, agent_name: str | None = None) -> str:
    """Submit a structured contribution to a thread following the protocol template.

    Args:
        thread_id: Thread ID
        phase: Protocol phase name (e.g. 'context', 'discussion')
        content: Structured content following protocol template sections
        tldr: One-line summary of this contribution
        agent_name: Your agent name (auto-detected from project if omitted)
    """
    name = agent_name or _project or "unknown"
    try:
        _post(f"/api/threads/{thread_id}/contributions", {
            "agentName": name, "phase": phase, "content": content, "tldr": tldr,
        })
        return f"Contribution submitted: {name}/{phase} in {thread_id}"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Thread '{thread_id}' not found."
        raise


@mcp.tool()
def btwin_thread_read(thread_id: str, phase: str | None = None, participant: str | None = None) -> str:
    """Read contributions and messages from a thread.

    Args:
        thread_id: Thread ID
        phase: Optional phase filter
        participant: Optional participant filter
    """
    params: dict = {}
    if phase:
        params["phase"] = phase
    if participant:
        params["participant"] = participant
    contribs = _get(f"/api/threads/{thread_id}/contributions", params=params)
    msgs = _get(f"/api/threads/{thread_id}/messages")
    lines = []
    if contribs:
        lines.append("## Contributions\n")
        for c in contribs:
            lines.append(f"### {c.get('agent', '?')} — {c.get('phase', '?')}")
            lines.append(f"*{c.get('tldr', '')}*\n")
            lines.append(c.get("_content", ""))
            lines.append("")
    if msgs:
        lines.append("## Messages\n")
        for m in msgs:
            lines.append(f"**{m.get('from', '?')}** ({m.get('msg_type', 'message')}): {m.get('_content', '')}")
            lines.append(f"  _{m.get('tldr', '')}_\n")
    if not lines:
        return "Thread is empty."
    return "\n".join(lines)


@mcp.tool()
def btwin_msg_send(thread_id: str, content: str, tldr: str, msg_type: str = "message", reply_to: str | None = None, agent_name: str | None = None) -> str:
    """Send a message to a thread.

    Args:
        thread_id: Thread ID
        content: Message content
        tldr: One-line summary
        msg_type: Message type — 'message' (default), 'context', 'proposal', 'decision'
        reply_to: Optional message_id to reply to
        agent_name: Your agent name (auto-detected from project if omitted)
    """
    name = agent_name or _project or "unknown"
    data: dict = {"fromAgent": name, "content": content, "tldr": tldr, "msgType": msg_type}
    if reply_to:
        data["replyTo"] = reply_to
    try:
        result = _post(f"/api/threads/{thread_id}/messages", data)
        return f"Message sent: {result['message_id']} in {thread_id}"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Thread '{thread_id}' not found."
        raise


@mcp.tool()
def btwin_thread_list(status: str | None = None) -> str:
    """List collaboration threads.

    Args:
        status: Optional filter — 'active', 'completed', or None for all
    """
    params: dict = {}
    if status:
        params["status"] = status
    threads = _get("/api/threads", params=params)
    if not threads:
        return "No threads found."
    lines = []
    for t in threads:
        participants = [p["name"] for p in t.get("participants", [])]
        lines.append(
            f"- **{t['thread_id']}** [{t['status']}] {t['topic']} "
            f"(protocol: {t['protocol']}, participants: {participants})"
        )
    return "\n".join(lines)


@mcp.tool()
def btwin_thread_close(thread_id: str, summary: str, decision: str | None = None) -> str:
    """Close a thread and save the result as a searchable btwin entry.

    Args:
        thread_id: Thread ID to close
        summary: Summary of what was discussed and agreed
        decision: Optional final decision text
    """
    data: dict = {"summary": summary}
    if decision:
        data["decision"] = decision
    try:
        result = _post(f"/api/threads/{thread_id}/close", data)
        record_id = result.get("result_record_id", "none")
        return (
            f"Thread closed: {thread_id}\n"
            f"Status: {result.get('status')}\n"
            f"Result entry: {record_id}"
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Thread '{thread_id}' not found."
        raise


@mcp.resource("btwin://record/{record_id}")
def read_record(record_id: str) -> str:
    """Read a full entry by record_id. Forwards to backend API."""
    resp = _http().get(f"/api/entries/by-record-id/{record_id}")
    if resp.status_code == 404:
        return f"Record not found: {record_id}"
    resp.raise_for_status()
    data = resp.json()
    fm = data.get("frontmatter", {})
    lines = [
        f"# {fm.get('record_type', 'entry')}: {record_id}",
        f"Date: {fm.get('date', 'unknown')}",
        f"Project: {fm.get('source_project', 'unknown')}",
        "",
        data.get("content", ""),
    ]
    return "\n".join(lines)


def _detect_project_name() -> str:
    """Auto-detect project name from git remote or cwd."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            url = result.stdout.strip()
            name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            if name.endswith(".git"):
                name = name[:-4]
            if name:
                return name
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return Path.cwd().name


def main() -> None:
    """CLI entry-point: parse --project/--backend, then run MCP over stdio."""
    import argparse

    parser = argparse.ArgumentParser(description="B-TWIN MCP Proxy")
    parser.add_argument("--project", default=None, help="Project name (auto-detected if omitted)")
    parser.add_argument(
        "--backend",
        default="http://localhost:8787",
        help="Backend API URL (default: http://localhost:8787)",
    )
    args = parser.parse_args()

    global _project, _backend
    _project = args.project or _detect_project_name()
    _backend = args.backend
    configure_runtime()

    log.info("B-TWIN MCP Proxy: project=%s backend=%s", _project, _backend)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
