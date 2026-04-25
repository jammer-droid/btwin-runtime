"""Static HTML report rendering for B-TWIN threads."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any


def slugify_topic(topic: object) -> str:
    text = (
        unicodedata.normalize("NFKD", str(topic or "thread"))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    parts = re.findall(r"[a-z0-9]+", text.lower())
    slug = "-".join(parts).strip("-")
    return (slug or "thread")[:80].strip("-") or "thread"


def default_report_path(
    project_root: Path,
    thread: dict[str, object],
    exported_at: datetime | None = None,
) -> Path:
    timestamp = exported_at or datetime.now(timezone.utc)
    topic_slug = slugify_topic(thread.get("topic") or thread.get("thread_id") or "thread")
    filename = f"{timestamp.date().isoformat()}-{topic_slug}-report.html"
    return project_root / "docs" / "local" / "reports" / filename


def render_thread_report_html(snapshot: dict[str, object]) -> str:
    thread = _as_dict(snapshot.get("thread"))
    protocol = _as_dict(snapshot.get("protocol"))
    status_summary = _as_dict(snapshot.get("status_summary"))
    exported_at = str(snapshot.get("exported_at") or datetime.now(timezone.utc).isoformat())
    title = f"B-TWIN Thread Report - {_plain(thread.get('topic') or thread.get('thread_id'))}"

    sections = [
        _render_overview(thread, status_summary, exported_at),
        _render_delegation(snapshot),
        _render_protocol(protocol),
        _render_agents(snapshot),
        _render_phase_cycle(snapshot),
        _render_timeline(snapshot),
        _render_artifacts(snapshot),
        _render_raw_appendix(snapshot),
    ]
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_esc(title)}</title>",
            "<style>",
            _CSS,
            "</style>",
            "</head>",
            "<body>",
            '<main class="page">',
            *sections,
            "</main>",
            "</body>",
            "</html>",
        ]
    )


def _render_overview(thread: dict[str, object], status_summary: dict[str, object], exported_at: str) -> str:
    chips = [
        _chip("status", thread.get("status")),
        _chip("protocol", thread.get("protocol")),
        _chip("phase", thread.get("current_phase")),
        _chip("mode", thread.get("interaction_mode")),
    ]
    participants = _participant_names(thread)
    rows = [
        ("Thread", thread.get("thread_id")),
        ("Topic", thread.get("topic")),
        ("Created", thread.get("created_at")),
        ("Closed", thread.get("closed_at")),
        ("Participants", ", ".join(participants) if participants else "-"),
        ("Exported", exported_at),
    ]
    status_rows = "".join(
        f"<tr><th>{_esc(key)}</th><td>{_esc(value)}</td></tr>"
        for key, value in _flatten_dict(status_summary).items()
    )
    status_block = (
        f"<details><summary>Status summary</summary><table>{status_rows}</table></details>"
        if status_rows
        else ""
    )
    return (
        '<section class="hero">'
        '<p class="eyebrow">B-TWIN thread work report</p>'
        f"<h1>{_esc(thread.get('topic') or thread.get('thread_id'))}</h1>"
        f'<div class="chips">{"".join(chips)}</div>'
        f'<table class="meta">{_table_rows(rows)}</table>'
        f"{status_block}"
        "</section>"
    )


def _render_delegation(snapshot: dict[str, object]) -> str:
    delegation = _as_dict(snapshot.get("delegation_status"))
    if not delegation:
        return _section("Delegation", '<p class="empty">No delegation state recorded for this thread.</p>')
    fields = [
        ("Status", delegation.get("status")),
        ("Target role", delegation.get("target_role")),
        ("Resolved agent", delegation.get("resolved_agent")),
        ("Required action", delegation.get("required_action")),
        ("Expected output", delegation.get("expected_output")),
        ("Current phase", delegation.get("current_phase")),
        ("Stop reason", delegation.get("stop_reason")),
        ("Blocked reason", delegation.get("reason_blocked")),
    ]
    return _section(
        "Delegation",
        f'<table class="meta">{_table_rows(fields)}</table>{_details("Raw delegation state", delegation)}',
    )


def _render_protocol(protocol: dict[str, object]) -> str:
    if not protocol:
        return _section("Protocol", '<p class="empty">Protocol definition was not available.</p>')

    phases = _as_list(protocol.get("phases"))
    phase_cards = []
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        phase_bits = [
            f"<h3>{_esc(phase.get('name'))}</h3>",
            f"<p>{_esc(phase.get('description'))}</p>" if phase.get("description") else "",
            '<div class="chips">'
            + "".join(
                [
                    _chip("action", ", ".join(str(item) for item in _as_list(phase.get("actions")))),
                    _chip("guard", phase.get("guard_set")),
                    _chip("gate", phase.get("gate")),
                    _chip("outcome", phase.get("outcome_policy")),
                ]
            )
            + "</div>",
            _mini_list(
                "Template",
                [item.get("section") for item in _as_list(phase.get("template")) if isinstance(item, dict)],
            ),
            _mini_list(
                "Procedure",
                [
                    f"{item.get('role', '-')}: {item.get('alias') or item.get('action', '-')}"
                    for item in _as_list(phase.get("procedure"))
                    if isinstance(item, dict)
                ],
            ),
        ]
        phase_cards.append(f'<article class="card">{"".join(phase_bits)}</article>')

    transitions = _mini_list(
        "Transitions",
        [
            f"{item.get('from', item.get('from_phase', '-'))} -> {item.get('to', '-')} on {item.get('on', '-')}: {item.get('alias') or item.get('key') or ''}".strip()
            for item in _as_list(protocol.get("transitions"))
            if isinstance(item, dict)
        ],
    )
    guard_sets = _mini_list(
        "Guard sets",
        [
            f"{item.get('name', '-')}: {', '.join(str(guard) for guard in _as_list(item.get('guards')))}"
            for item in _as_list(protocol.get("guard_sets"))
            if isinstance(item, dict)
        ],
    )
    gates = _mini_list(
        "Gates",
        [
            f"{item.get('name', '-')}: "
            + ", ".join(
                f"{route.get('outcome')} -> {route.get('target_phase')}"
                for route in _as_list(item.get("routes"))
                if isinstance(route, dict)
            )
            for item in _as_list(protocol.get("gates"))
            if isinstance(item, dict)
        ],
    )
    policies = _mini_list(
        "Outcome policies",
        [
            f"{item.get('name', '-')}: {', '.join(str(outcome) for outcome in _as_list(item.get('outcomes')))}"
            for item in _as_list(protocol.get("outcome_policies"))
            if isinstance(item, dict)
        ],
    )
    phase_grid = "".join(phase_cards)
    content = (
        f'<div class="section-head"><p>{_esc(protocol.get("description"))}</p></div>'
        f'<div class="grid">{phase_grid}</div>'
        f'<div class="split">{transitions}{guard_sets}{gates}{policies}</div>'
    )
    return _section("Protocol", content)


def _render_agents(snapshot: dict[str, object]) -> str:
    thread = _as_dict(snapshot.get("thread"))
    registered = {
        str(agent.get("name")): agent
        for agent in _as_list(snapshot.get("agents"))
        if isinstance(agent, dict) and agent.get("name")
    }
    runtime_by_agent = _runtime_by_agent(
        snapshot.get("runtime_sessions"),
        thread.get("thread_id"),
    )
    names = _participant_names(thread)
    for name in registered:
        if name not in names:
            names.append(name)
    if not names:
        return _section("Agents", '<p class="empty">No participants recorded.</p>')

    rows = []
    for name in names:
        agent = _as_dict(registered.get(name))
        sessions = runtime_by_agent.get(name, [])
        rows.append(
            "<tr>"
            f"<td>{_esc(name)}</td>"
            f"<td>{_esc(agent.get('role') or '-')}</td>"
            f"<td>{_esc(agent.get('provider') or _first_session_value(sessions, 'provider') or '-')}</td>"
            f"<td>{_esc(agent.get('model') or '-')}</td>"
            f"<td>{_esc(agent.get('reasoning_level') or '-')}</td>"
            f"<td>{_esc(_session_summary(sessions))}</td>"
            "</tr>"
        )
    table = (
        '<table><thead><tr><th>Agent</th><th>Role</th><th>Provider</th><th>Model</th>'
        '<th>Reasoning</th><th>Runtime</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table>"
    )
    return _section("Agents", table)


def _render_phase_cycle(snapshot: dict[str, object]) -> str:
    phase_cycle = _as_dict(snapshot.get("phase_cycle"))
    if not phase_cycle:
        return _section("Workflow Summary", '<p class="empty">No phase-cycle snapshot recorded.</p>')
    visual = _as_dict(phase_cycle.get("visual"))
    state = _as_dict(phase_cycle.get("state"))
    chips = "".join(
        [
            _chip("active", state.get("active_phase") or state.get("current_phase")),
            _chip("cycle", state.get("active_cycle_index") or state.get("current_cycle_index")),
            _chip("status", state.get("status")),
        ]
    )
    return _section(
        "Workflow Summary",
        f'<div class="chips">{chips}</div>{_details("Phase-cycle payload", phase_cycle or visual)}',
    )


def _render_timeline(snapshot: dict[str, object]) -> str:
    items = _timeline_items(snapshot)
    if not items:
        return _section(
            "Work History",
            '<p class="empty">No messages, contributions, reports, or workflow events recorded.</p>',
        )
    rendered = []
    for item in items:
        body = f'<pre>{_esc(item.get("body"))}</pre>' if item.get("body") else ""
        rendered.append(
            '<article class="timeline-item">'
            f'<div class="time">{_esc(item.get("timestamp") or "-")}</div>'
            f'<div class="kind">{_esc(item.get("kind"))}</div>'
            f'<h3>{_esc(item.get("title"))}</h3>'
            f'<p>{_esc(item.get("summary"))}</p>'
            f"{body}"
            f'{_details("Metadata", item.get("metadata"))}'
            "</article>"
        )
    return _section("Work History", '<div class="timeline">' + "".join(rendered) + "</div>")


def _render_artifacts(snapshot: dict[str, object]) -> str:
    contributions = [item for item in _as_list(snapshot.get("contributions")) if isinstance(item, dict)]
    if not contributions:
        return _section("Markdown Artifacts", '<p class="empty">No contribution artifacts recorded.</p>')
    cards = []
    for item in sorted(contributions, key=lambda value: str(value.get("created_at", ""))):
        cards.append(
            '<article class="card">'
            f"<h3>{_esc(item.get('phase'))} / {_esc(item.get('agent'))}</h3>"
            f"<p>{_esc(item.get('tldr'))}</p>"
            f"<pre>{_esc(item.get('_content'))}</pre>"
            "</article>"
        )
    return _section("Markdown Artifacts", '<div class="grid">' + "".join(cards) + "</div>")


def _render_raw_appendix(snapshot: dict[str, object]) -> str:
    payload = {
        "thread": snapshot.get("thread"),
        "status_summary": snapshot.get("status_summary"),
        "mailbox_reports": snapshot.get("mailbox_reports"),
        "workflow_events": snapshot.get("workflow_events"),
    }
    return _section("Appendix", _details("Source payload excerpt", payload, open_by_default=False))


def _timeline_items(snapshot: dict[str, object]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for message in _as_list(snapshot.get("messages")):
        if not isinstance(message, dict):
            continue
        items.append(
            {
                "timestamp": message.get("created_at"),
                "kind": f"message:{message.get('msg_type', 'message')}",
                "title": message.get("tldr") or message.get("message_id"),
                "summary": f"from {message.get('from', '-')}",
                "body": message.get("_content"),
                "metadata": {key: value for key, value in message.items() if key != "_content"},
            }
        )
    for contribution in _as_list(snapshot.get("contributions")):
        if not isinstance(contribution, dict):
            continue
        items.append(
            {
                "timestamp": contribution.get("created_at"),
                "kind": "contribution",
                "title": contribution.get("tldr") or contribution.get("contribution_id"),
                "summary": f"{contribution.get('agent', '-')} / {contribution.get('phase', '-')}",
                "body": contribution.get("_content"),
                "metadata": {key: value for key, value in contribution.items() if key != "_content"},
            }
        )
    for report in _as_list(snapshot.get("mailbox_reports")):
        if not isinstance(report, dict):
            continue
        items.append(
            {
                "timestamp": report.get("created_at"),
                "kind": report.get("report_type", "report"),
                "title": report.get("summary") or report.get("report_type"),
                "summary": f"phase {report.get('phase', '-')}",
                "body": "",
                "metadata": report,
            }
        )
    for event in _as_list(snapshot.get("workflow_events")):
        if not isinstance(event, dict):
            continue
        items.append(
            {
                "timestamp": event.get("timestamp") or event.get("created_at"),
                "kind": event.get("event_type", "workflow_event"),
                "title": event.get("summary") or event.get("event_type"),
                "summary": event.get("source", ""),
                "body": "",
                "metadata": event,
            }
        )
    return sorted(items, key=lambda item: str(item.get("timestamp") or ""))


def _section(title: str, content: str) -> str:
    return f'<section><h2>{_esc(title)}</h2>{content}</section>'


def _mini_list(title: str, values: list[object]) -> str:
    clean_values = [str(value) for value in values if value not in (None, "")]
    if not clean_values:
        return ""
    items = "".join(f"<li>{_esc(value)}</li>" for value in clean_values)
    return f'<div class="mini"><h4>{_esc(title)}</h4><ul>{items}</ul></div>'


def _table_rows(rows: list[tuple[str, object]]) -> str:
    return "".join(
        f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>"
        for label, value in rows
        if value not in (None, "")
    )


def _chip(label: str, value: object) -> str:
    if value in (None, ""):
        return ""
    return f'<span class="chip"><b>{_esc(label)}</b>{_esc(value)}</span>'


def _details(title: str, payload: object, *, open_by_default: bool = True) -> str:
    if payload in (None, "", [], {}):
        return ""
    open_attr = " open" if open_by_default else ""
    return f"<details{open_attr}><summary>{_esc(title)}</summary><pre>{_esc(_json_dump(payload))}</pre></details>"


def _flatten_dict(payload: dict[str, object], prefix: str = "") -> dict[str, object]:
    rows: dict[str, object] = {}
    for key, value in payload.items():
        label = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            rows.update(_flatten_dict(value, label))
        elif isinstance(value, list):
            rows[label] = ", ".join(_plain(item) for item in value)
        else:
            rows[label] = value
    return rows


def _participant_names(thread: dict[str, object]) -> list[str]:
    names: list[str] = []
    for participant in _as_list(thread.get("participants")):
        if isinstance(participant, dict) and isinstance(participant.get("name"), str):
            names.append(participant["name"])
        elif isinstance(participant, str):
            names.append(participant)
    return names


def _runtime_by_agent(runtime_sessions: object, thread_id: object) -> dict[str, list[dict[str, object]]]:
    payload = _as_dict(runtime_sessions)
    agents = _as_dict(payload.get("agents"))
    result: dict[str, list[dict[str, object]]] = {}
    for agent_name, sessions in agents.items():
        clean_sessions = []
        for session in _as_list(sessions):
            if not isinstance(session, dict):
                continue
            if thread_id and session.get("thread_id") not in (thread_id, None):
                continue
            clean_sessions.append(session)
        if clean_sessions:
            result[str(agent_name)] = clean_sessions
    return result


def _session_summary(sessions: list[dict[str, object]]) -> str:
    if not sessions:
        return "-"
    labels = []
    for session in sessions:
        status = session.get("status") or "active"
        mode = session.get("transport_mode") or session.get("primary_transport_mode") or ""
        labels.append(f"{status} {mode}".strip())
    return ", ".join(labels)


def _first_session_value(sessions: list[dict[str, object]], key: str) -> object:
    for session in sessions:
        value = session.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _plain(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return _json_dump(value)
    return str(value)


def _esc(value: object) -> str:
    return escape(_plain(value), quote=True)


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


_CSS = """
:root {
  color-scheme: light;
  --ink: #1d2733;
  --muted: #607085;
  --line: #dbe3eb;
  --soft: #f6f8fb;
  --panel: #ffffff;
  --accent: #0c7c7c;
  --accent-soft: #e2f4f2;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #eef2f6;
  color: var(--ink);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.page {
  max-width: 1120px;
  margin: 0 auto;
  padding: 32px 20px 56px;
}
section {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  margin: 16px 0;
  padding: 22px;
}
.hero {
  border-top: 5px solid var(--accent);
}
.eyebrow {
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
  margin: 0 0 8px;
  text-transform: uppercase;
}
h1, h2, h3, h4 { line-height: 1.2; margin: 0 0 10px; }
h1 { font-size: 32px; }
h2 { font-size: 22px; }
h3 { font-size: 16px; }
h4 { color: var(--muted); font-size: 13px; }
p { margin: 0 0 12px; }
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0;
}
.chip {
  background: var(--accent-soft);
  border: 1px solid #b8dfda;
  border-radius: 999px;
  color: #164f4f;
  display: inline-flex;
  gap: 6px;
  padding: 4px 9px;
}
table {
  border-collapse: collapse;
  width: 100%;
}
th, td {
  border-bottom: 1px solid var(--line);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}
th {
  color: var(--muted);
  font-weight: 700;
  width: 180px;
}
.grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}
.split {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  margin-top: 14px;
}
.card, .mini {
  background: var(--soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
}
ul { margin: 0; padding-left: 18px; }
details {
  background: var(--soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  margin-top: 12px;
  padding: 10px 12px;
}
summary {
  color: var(--accent);
  cursor: pointer;
  font-weight: 700;
}
pre {
  background: #17202b;
  border-radius: 8px;
  color: #eef6ff;
  margin: 10px 0 0;
  overflow-x: auto;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
.timeline {
  border-left: 3px solid var(--line);
  margin-left: 8px;
  padding-left: 18px;
}
.timeline-item {
  border-bottom: 1px solid var(--line);
  padding: 14px 0;
}
.timeline-item:last-child { border-bottom: 0; }
.time, .kind {
  color: var(--muted);
  font-size: 12px;
}
.kind {
  color: var(--accent);
  font-weight: 700;
  text-transform: uppercase;
}
.empty {
  color: var(--muted);
  font-style: italic;
}
@media (max-width: 700px) {
  .page { padding: 18px 12px 36px; }
  section { padding: 16px; }
  h1 { font-size: 24px; }
  th, td { display: block; width: 100%; }
  th { border-bottom: 0; padding-bottom: 0; }
}
"""
