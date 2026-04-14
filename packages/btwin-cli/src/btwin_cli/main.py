"""B-TWIN CLI — packaged command-line implementation."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_LEGACY_SRC = _REPO_ROOT / "src"
if _LEGACY_SRC.exists():
    legacy_src = str(_LEGACY_SRC)
    if legacy_src not in sys.path:
        sys.path.insert(0, legacy_src)

import json
import os
import plistlib
import queue as queue_module
import re
import shutil
import subprocess
import threading
import time

import typer
import yaml
from rich.console import Console
from rich.markdown import Markdown

from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig, load_config, resolve_config_path
from btwin_core.handoff_archive import get_handoff_record, list_handoff_records, write_handoff_record
from btwin_core.locale_settings import LocaleSettingsStore
from btwin_core.protocol_flow import describe_next
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolStore
from btwin_core.protocol_validator import ProtocolValidator
from btwin_core.sources import SourceRegistry
from btwin_core.runtime_binding_store import RuntimeBindingState, RuntimeBindingStore
from btwin_core.thread_chat import parse_thread_chat_input
from btwin_core.thread_store import ThreadStore
from btwin_core.storage import Storage
from btwin_core.workflow_engine import WorkflowEngine
from btwin_cli.provider_init import (
    available_provider_names,
    build_provider_config,
    provider_display_name,
    validate_provider_cli,
    write_provider_config,
)
from btwin_cli.resource_paths import resolve_bundled_skills_dir
from btwin_core.resource_paths import resolve_bundled_protocols_dir

app = typer.Typer(
    name="btwin",
    help="B-TWIN: AI partner that remembers your thoughts.",
)
sources_app = typer.Typer(help="Manage B-TWIN data sources for dashboard workflows.")
promotion_app = typer.Typer(help="Manage promotion queue operations.")
indexer_app = typer.Typer(help="Manage core indexer workflows.")
runtime_app = typer.Typer(help="Inspect runtime mode and integration settings.")
live_app = typer.Typer(help="Use the attached live collaboration surface.")
agent_app = typer.Typer(help="Manage B-TWIN agent definitions.")
protocol_app = typer.Typer(help="Manage B-TWIN protocol definitions.")
thread_app = typer.Typer(help="Manage B-TWIN protocol threads.")
contribution_app = typer.Typer(help="Manage B-TWIN protocol contributions.")
service_app = typer.Typer(help="Manage the macOS launchd service for B-TWIN API.")
handoff_app = typer.Typer(
    help="Write or inspect project handoff snapshots and archive.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(sources_app, name="sources")
app.add_typer(promotion_app, name="promotion")
app.add_typer(indexer_app, name="indexer")
app.add_typer(runtime_app, name="runtime")
app.add_typer(live_app, name="live")
app.add_typer(agent_app, name="agent")
app.add_typer(protocol_app, name="protocol")
app.add_typer(thread_app, name="thread")
app.add_typer(contribution_app, name="contribution")
app.add_typer(service_app, name="service")
app.add_typer(handoff_app, name="handoff")

console = Console(soft_wrap=True)
logger = logging.getLogger(__name__)
_SERVICE_LABEL = "com.btwin.serve-api"


def _emit_payload(payload: object, as_json: bool) -> None:
    if as_json:
        console.print_json(data=payload)
        return
    if isinstance(payload, list):
        if not payload:
            console.print("[dim]No entries found.[/dim]")
            return
        for item in payload:
            console.print(yaml.safe_dump(item, sort_keys=False).strip())
            console.print("")
        return
    if isinstance(payload, dict):
        console.print(yaml.safe_dump(payload, sort_keys=False).strip())
        return
    console.print(str(payload))


def _resolve_content(content: str | None) -> str:
    if content is not None:
        return content
    stdin_content = typer.get_text_stream("stdin").read()
    if not stdin_content.strip():
        raise typer.BadParameter("Content is required via --content or stdin.")
    return stdin_content


def _thread_enter_command(thread_id: str, actor: str = "user") -> str:
    return f"btwin thread enter --thread {thread_id} --as {actor}"


def _thread_create_payload(thread: dict[str, object]) -> dict[str, object]:
    payload = dict(thread)
    thread_id = payload.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        payload["enter_command"] = _thread_enter_command(thread_id)
    return payload


def _api_base_url() -> str:
    return os.environ.get("BTWIN_API_URL", "http://localhost:8787")


def _api_post(path: str, data: dict) -> dict:
    import httpx

    with httpx.Client(base_url=_api_base_url(), timeout=30.0) as client:
        resp = client.post(path, json=data)
        resp.raise_for_status()
        return resp.json()


def _api_get(path: str, params: dict | None = None):
    import httpx

    with httpx.Client(base_url=_api_base_url(), timeout=30.0) as client:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


def _current_btwin_command_path() -> Path | None:
    argv0 = sys.argv[0]
    if not argv0:
        return None

    if os.sep in argv0 or argv0.startswith("."):
        try:
            return Path(argv0).expanduser().resolve()
        except OSError:
            return Path(argv0).expanduser()

    resolved = shutil.which(argv0)
    if resolved is None:
        return None
    return Path(resolved).expanduser().resolve()


def _attached_runtime_diagnostics_context() -> dict[str, object]:
    current_btwin = _current_btwin_command_path()
    path_btwin = shutil.which("btwin")
    config_path = _config_path()
    data_dir = _get_active_data_dir()
    api_url = _api_base_url()

    messages = [
        "- If you use a custom endpoint, check [bold]BTWIN_API_URL[/bold]",
        "- For local-only usage, switch to [bold]runtime.mode: standalone[/bold] in the active config",
    ]
    path_matches_current = None
    path_btwin_resolved = None

    if path_btwin and current_btwin is not None:
        try:
            path_btwin_resolved = Path(path_btwin).expanduser().resolve()
        except OSError:
            path_btwin_resolved = Path(path_btwin).expanduser()

        path_matches_current = path_btwin_resolved == current_btwin
        if not path_matches_current:
            messages.append("- Possible PATH mismatch: the current process and the `btwin` on PATH are different.")
            messages.append("- Re-run `btwin init` if needed, then restart the MCP client session.")
        else:
            messages.append(
                "- If MCP tools still look stale, restart your MCP client session to clear a stale MCP proxy or stale Codex client session."
            )
    elif not path_btwin:
        path_matches_current = False
        messages.append("- `btwin` is not currently resolvable from PATH.")

    return {
        "url": api_url,
        "config_path": str(config_path),
        "data_dir": str(data_dir),
        "current_btwin": str(current_btwin) if current_btwin is not None else None,
        "path_btwin": path_btwin,
        "path_btwin_resolved": str(path_btwin_resolved) if path_btwin_resolved is not None else None,
        "path_matches_current": path_matches_current,
        "messages": messages,
    }


def _attached_runtime_diagnostics() -> list[str]:
    diagnostics = _attached_runtime_diagnostics_context()
    return [
        f"- URL: {diagnostics['url']}",
        f"- Config: {diagnostics['config_path']}",
        f"- Data dir: {diagnostics['data_dir']}",
        f"- Current btwin: {diagnostics['current_btwin'] or 'unknown'}",
        f"- PATH btwin: {diagnostics['path_btwin'] or 'not found'}",
        *diagnostics["messages"],
    ]


def _render_attached_http_status_error(exc) -> None:
    response = exc.response
    detail = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("detail")
    except Exception:
        detail = None

    detail_text = detail if isinstance(detail, str) else response.text.strip() or exc.__class__.__name__
    console.print(
        "[red]Attached runtime shared API responded with an error.[/red]\n"
        f"- URL: {_api_base_url()}\n"
        f"- Status: {response.status_code}\n"
        f"- Detail: {detail_text}"
    )


def _attached_http_status_exit_code(exc) -> int:
    response = exc.response
    if response.status_code == 404:
        return 4
    return 1


def _render_attached_transport_error(exc) -> None:
    lines = _attached_runtime_diagnostics()
    console.print(
        "[red]Attached runtime could not reach the shared B-TWIN API.[/red]\n"
        + "\n".join(lines)
        + f"\n- Error: {exc.__class__.__name__}: {exc}"
    )


def _use_attached_api(config: BTwinConfig) -> bool:
    return config.runtime.mode == "attached"


def _attached_api_call_or_exit(path: str, data: dict) -> dict:
    import httpx

    try:
        return _api_post(path, data)
    except httpx.HTTPStatusError as exc:
        _render_attached_http_status_error(exc)
        raise typer.Exit(_attached_http_status_exit_code(exc))
    except httpx.RequestError as exc:
        _render_attached_transport_error(exc)
        raise typer.Exit(1)


def _attached_api_get_or_exit(path: str, params: dict | None = None):
    import httpx

    try:
        return _api_get(path, params=params)
    except httpx.HTTPStatusError as exc:
        _render_attached_http_status_error(exc)
        raise typer.Exit(_attached_http_status_exit_code(exc))
    except httpx.RequestError as exc:
        _render_attached_transport_error(exc)
        raise typer.Exit(1)


def _require_attached_live(config: BTwinConfig) -> None:
    if _use_attached_api(config):
        return
    console.print(
        "[red]`btwin live` requires attached runtime mode.[/red]\n"
        "- Switch the active config to [bold]runtime.mode: attached[/bold]\n"
        "- Or set [bold]BTWIN_CONFIG_PATH[/bold] to an attached config before using `btwin live`"
    )
    raise typer.Exit(4)


def _get_runtime_binding_store() -> RuntimeBindingStore:
    return RuntimeBindingStore(_project_root() / ".btwin")


def _resolve_runtime_thread(thread_id: str, config: BTwinConfig | None = None) -> dict | None:
    current_config = config or _get_config()
    if _use_attached_api(current_config):
        return _attached_api_get_or_exit(f"/api/threads/{thread_id}")

    store = _get_thread_store()
    return store.get_thread(thread_id)


def _thread_participant_names(thread: dict | None) -> set[str]:
    if not isinstance(thread, dict):
        return set()
    participants = thread.get("participants", [])
    if not isinstance(participants, list):
        return set()
    return {
        participant["name"]
        for participant in participants
        if isinstance(participant, dict) and isinstance(participant.get("name"), str)
    }


def _resolve_runtime_thread_safely(
    thread_id: str,
    config: BTwinConfig | None = None,
) -> tuple[dict | None, str | None]:
    current_config = config or _get_config()
    try:
        if _use_attached_api(current_config):
            return _attached_api_get_or_exit(f"/api/threads/{thread_id}"), None
        return _get_thread_store().get_thread(thread_id), None
    except Exception as exc:
        return None, f"Failed to fetch thread details: {exc.__class__.__name__}: {exc}"


def _resolve_runtime_thread_id(thread_id: str | None, config: BTwinConfig | None = None) -> tuple[str, str]:
    if thread_id is not None:
        return thread_id, "explicit"

    state = _get_runtime_binding_store().read_state()
    if state.binding is None:
        if state.binding_error:
            console.print(
                "[red]No usable runtime binding found.[/red]\n"
                f"- Error: {state.binding_error}\n"
                "- Pass [bold]--thread[/bold] explicitly or fix the runtime binding file."
            )
        else:
            console.print(
                "[red]No runtime binding found.[/red]\n"
                "- Pass [bold]--thread[/bold] explicitly or bind the current project with [bold]btwin runtime bind[/bold]."
            )
        raise typer.Exit(4)

    return state.binding.thread_id, "runtime_binding"


def _runtime_binding_payload(
    state: RuntimeBindingState,
    *,
    config: BTwinConfig | None = None,
    thread: dict | None = None,
    agent_store: AgentStore | None = None,
    include_thread_lookup_error: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "bound": state.bound,
        "binding": state.binding.model_dump() if state.binding is not None else None,
        "binding_error": state.binding_error,
    }
    if state.binding is None:
        return payload

    current_config = config or _get_config()
    resolved_thread = thread
    thread_error = None
    if resolved_thread is None:
        if include_thread_lookup_error:
            resolved_thread, thread_error = _resolve_runtime_thread_safely(state.binding.thread_id, current_config)
        else:
            resolved_thread = _resolve_runtime_thread(state.binding.thread_id, current_config)
    if resolved_thread is not None:
        payload["thread"] = resolved_thread
    if thread_error is not None:
        payload["thread_error"] = thread_error

    store = agent_store or _get_agent_store()
    agent = store.get_agent(state.binding.agent_name)
    if agent is not None:
        payload["agent"] = agent
    return payload


def _record_thread_result_entry(
    data_dir: Path,
    thread_id: str,
    closed: dict,
    summary: str,
    decision: str | None,
) -> str | None:
    try:
        from btwin_core.btwin import BTwin

        config = BTwinConfig(data_dir=data_dir)
        twin = BTwin(config)
        protocol_name = str(closed.get("protocol", "unknown"))
        participants = [
            participant["name"]
            for participant in closed.get("participants", [])
            if isinstance(participant, dict) and participant.get("name")
        ]

        content = f"## Summary\n\n{summary}"
        if decision:
            content += f"\n\n## Decision\n\n{decision}"
        content += f"\n\n## Participants\n\n{', '.join(participants)}"
        content += f"\n\n## Thread\n\n{thread_id} (protocol: {protocol_name})"

        result = twin.record(
            content,
            topic="thread-result",
            tags=["thread-result", f"protocol:{protocol_name}"],
            tldr=summary[:200],
        )
        saved_path = result.get("path")
        if not saved_path:
            return None

        raw = Path(saved_path).read_text(encoding="utf-8")
        parts = raw.split("---\n", 2)
        if len(parts) < 3:
            return None

        metadata = yaml.safe_load(parts[1]) or {}
        record_id = metadata.get("record_id")
        if not record_id:
            return None

        update_result = twin.update_entry(record_id=record_id, related_records=[f"thread:{thread_id}"])
        if not update_result.get("ok"):
            return None
        return record_id
    except Exception:
        logger.warning("Failed to create thread result entry for %s", thread_id, exc_info=True)
        return None


def _config_path() -> Path:
    return resolve_config_path()


def _btwin_data_dir() -> Path:
    return _get_active_data_dir()


def _get_config() -> BTwinConfig:
    config_path = _config_path()
    if config_path.exists():
        return load_config(config_path)
    return BTwinConfig()


def _get_active_data_dir(config: BTwinConfig | None = None) -> Path:
    return (config or _get_config()).data_dir


def _get_registry() -> SourceRegistry:
    return SourceRegistry(_get_active_data_dir() / "sources.yaml")


def _get_agent_store() -> AgentStore:
    return AgentStore(_btwin_data_dir())


def _bundled_protocols_dir() -> Path:
    bundled = resolve_bundled_protocols_dir()
    return bundled if bundled is not None else _REPO_ROOT / "global" / "protocols"


def _get_protocol_store() -> ProtocolStore:
    return ProtocolStore(
        _project_root() / ".btwin" / "protocols",
        fallback_dir=_bundled_protocols_dir(),
    )


def _get_thread_store() -> ThreadStore:
    return ThreadStore(_project_root() / ".btwin" / "threads")


def _get_workflow_engine(data_dir: Path | None = None) -> WorkflowEngine:
    return WorkflowEngine(Storage(data_dir or _btwin_data_dir()))


def _normalize_runtime_sessions(agent_name: str, raw_sessions: object) -> tuple[list[dict[str, object]], str | None]:
    if not isinstance(raw_sessions, list):
        return [], f"Unexpected runtime session payload shape for {agent_name}: expected a list"

    sessions: list[dict[str, object]] = []
    dropped = 0
    for session in raw_sessions:
        if isinstance(session, dict):
            sessions.append(dict(session))
        elif isinstance(session, str):
            sessions.append({"thread_id": session, "status": "active"})
        else:
            dropped += 1

    warning = None
    if dropped:
        warning = f"Ignored {dropped} malformed runtime session record(s) for {agent_name}"
    return sessions, warning


def _get_attached_runtime_sessions(agent_name: str, config: BTwinConfig | None = None) -> tuple[list[dict[str, object]], str | None, str | None]:
    current_config = config or _get_config()
    if not _use_attached_api(current_config):
        return [], None, None

    try:
        payload = _api_get("/api/agent-runtime-status")
    except Exception as exc:
        logger.warning("Failed to fetch runtime sessions for %s", agent_name, exc_info=True)
        return [], None, f"Failed to fetch runtime sessions: {exc.__class__.__name__}: {exc}"

    agents = payload.get("agents", {}) if isinstance(payload, dict) else {}
    if not isinstance(agents, dict):
        return [], f"Unexpected runtime session payload shape for {agent_name}: expected agents mapping", None

    raw_sessions = agents.get(agent_name, [])
    sessions, warning = _normalize_runtime_sessions(agent_name, raw_sessions)
    return sessions, warning, None


def _attached_runtime_sessions_payload() -> dict[str, object]:
    payload = _api_get("/api/agent-runtime-status")
    return payload if isinstance(payload, dict) else {}


def _attached_agents_by_thread() -> dict[str, list[str]]:
    payload = _attached_runtime_sessions_payload()
    agents_payload = payload.get("agents", {})
    if not isinstance(agents_payload, dict):
        return {}

    attached_by_thread: dict[str, set[str]] = {}
    for agent_name, raw_sessions in agents_payload.items():
        if not isinstance(agent_name, str):
            continue
        sessions, _warning = _normalize_runtime_sessions(agent_name, raw_sessions)
        for session in sessions:
            thread_id = session.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                continue
            attached_by_thread.setdefault(thread_id, set()).add(agent_name)
    return {
        thread_id: sorted(agent_names)
        for thread_id, agent_names in attached_by_thread.items()
    }


def _build_agent_queue_summary(
    agent_name: str,
    queue_data_dir: Path | None = None,
    agent_store: AgentStore | None = None,
) -> list[dict[str, object]]:
    store = agent_store or _get_agent_store()
    queue = store.get_queue(agent_name)
    if not queue:
        return []

    workflow_engine = _get_workflow_engine(queue_data_dir or store.data_dir)
    workflow_entries = {
        entry.get("record_id"): entry
        for entry in workflow_engine._read_all_workflow_entries()
        if isinstance(entry, dict) and isinstance(entry.get("record_id"), str) and entry.get("record_id")
    }
    items: list[dict[str, object]] = []
    for order, item in enumerate(queue):
        if not isinstance(item, dict):
            continue
        workflow_id = str(item.get("workflow_id") or "")
        task_id = str(item.get("task_id") or "")
        workflow_entry = workflow_entries.get(workflow_id) if workflow_id else None
        task_entry = workflow_entries.get(task_id) if task_id else None
        items.append(
            {
                "workflow_id": workflow_id,
                "workflow_name": workflow_entry.get("name", "") if workflow_entry else "",
                "workflow_status": workflow_entry.get("status", "") if workflow_entry else "",
                "task_id": task_id,
                "task_name": task_entry.get("name", "") if task_entry else "",
                "task_status": task_entry.get("status", "") if task_entry else "",
                "assigned_agent": task_entry.get("assigned_agent") if task_entry else None,
                "order": order,
            }
        )
    return items


def _build_agent_thread_summary(
    agent_name: str,
    thread_store: ThreadStore | None = None,
    config: BTwinConfig | None = None,
) -> tuple[list[dict[str, object]], str | None]:
    resolved_config = config or _get_config()
    if _use_attached_api(resolved_config):
        try:
            threads = _api_get("/api/threads", params={"status": "active"})
        except Exception as exc:
            return [], f"Failed to fetch attached thread summaries: {exc.__class__.__name__}: {exc}"
        summaries: list[dict[str, object]] = []
        skipped: list[str] = []
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            thread_id = thread.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                continue
            participants = thread.get("participants", [])
            if not isinstance(participants, list):
                continue
            participant_names = {
                participant["name"]
                for participant in participants
                if isinstance(participant, dict) and isinstance(participant.get("name"), str)
            }
            if agent_name not in participant_names:
                continue

            try:
                inbox_payload = _api_get(f"/api/threads/{thread_id}/inbox", params={"agent": agent_name})
                agent_status = _api_get(f"/api/threads/{thread_id}/status", params={"agent": agent_name})
            except Exception as exc:
                skipped.append(f"{thread_id} ({exc.__class__.__name__}: {exc})")
                continue
            pending_messages = inbox_payload.get("messages", []) if isinstance(inbox_payload, dict) else []
            summaries.append(
                {
                    "thread_id": thread_id,
                    "topic": thread.get("topic", ""),
                    "protocol": thread.get("protocol", ""),
                    "status": thread.get("status", ""),
                    "current_phase": agent_status.get("current_phase", thread.get("current_phase"))
                    if isinstance(agent_status, dict)
                    else thread.get("current_phase"),
                    "interaction_mode": agent_status.get("interaction_mode", thread.get("interaction_mode"))
                    if isinstance(agent_status, dict)
                    else thread.get("interaction_mode"),
                    "participant_status": agent_status.get("participant_status")
                    if isinstance(agent_status, dict)
                    else None,
                    "pending_message_count": agent_status.get("pending_message_count", len(pending_messages))
                    if isinstance(agent_status, dict)
                    else len(pending_messages),
                    "pending_messages": pending_messages,
                    "created_at": thread.get("created_at"),
                }
            )
        warning = None
        if skipped:
            warning = f"Some attached thread summaries were skipped for {agent_name}: " + ", ".join(skipped)
        return summaries, warning

    store = thread_store or _get_thread_store()
    threads = store.list_threads(status="active")

    summaries: list[dict[str, object]] = []
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        thread_id = thread.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        participants = thread.get("participants", [])
        if not isinstance(participants, list):
            continue
        participant_names = {
            participant["name"]
            for participant in participants
            if isinstance(participant, dict) and isinstance(participant.get("name"), str)
        }
        if agent_name not in participant_names:
            continue

        pending_messages = store.list_inbox(thread_id, agent_name) or []
        agent_status = store.get_agent_status(thread_id, agent_name) or {}
        summaries.append(
            {
                "thread_id": thread_id,
                "topic": thread.get("topic", ""),
                "protocol": thread.get("protocol", ""),
                "status": thread.get("status", ""),
                "current_phase": agent_status.get("current_phase", thread.get("current_phase")),
                "interaction_mode": agent_status.get("interaction_mode", thread.get("interaction_mode")),
                "participant_status": agent_status.get("participant_status"),
                "pending_message_count": len(pending_messages),
                "pending_messages": pending_messages,
                "created_at": thread.get("created_at"),
            }
        )

    return summaries, None


def _load_protocol_flow_context(
    thread_id: str,
    config: BTwinConfig | None = None,
) -> tuple[dict, Protocol, ProtocolPhase, list[str], list[dict]]:
    current_config = config or _get_config()
    if _use_attached_api(current_config):
        thread = _attached_api_get_or_exit(f"/api/threads/{thread_id}")
        protocol_name = thread.get("protocol")
        if not isinstance(protocol_name, str) or not protocol_name:
            console.print(f"[red]Protocol not found for thread:[/red] {protocol_name}")
            raise typer.Exit(4)

        protocol_payload = _attached_api_get_or_exit(f"/api/protocols/{protocol_name}")
        protocol = Protocol.model_validate(protocol_payload)
        current_phase = thread.get("current_phase")
        phase = next((item for item in protocol.phases if item.name == current_phase), None)
        if phase is None:
            payload = {
                "thread_id": thread_id,
                "protocol": protocol.name,
                "current_phase": current_phase,
                "passed": False,
                "error": "phase_not_found",
            }
            _emit_payload(payload, as_json=True)
            raise typer.Exit(2)

        phase_participants = thread.get("phase_participants", [])
        if not isinstance(phase_participants, list):
            phase_participants = []
        contributions = []
        if isinstance(current_phase, str) and current_phase:
            contributions_payload = _attached_api_get_or_exit(
                f"/api/threads/{thread_id}/contributions",
                {"phase": current_phase},
            )
            if isinstance(contributions_payload, list):
                contributions = contributions_payload
        return thread, protocol, phase, [str(name) for name in phase_participants if isinstance(name, str)], contributions

    thread_store = _get_thread_store()
    thread = thread_store.get_thread(thread_id)
    if thread is None:
        console.print(f"[red]Thread not found:[/red] {thread_id}")
        raise typer.Exit(4)

    protocol_name = thread.get("protocol")
    protocol = _get_protocol_store().get_protocol(protocol_name) if protocol_name else None
    if protocol is None:
        console.print(f"[red]Protocol not found for thread:[/red] {protocol_name}")
        raise typer.Exit(4)

    current_phase = thread.get("current_phase")
    phase = next((item for item in protocol.phases if item.name == current_phase), None)
    if phase is None:
        payload = {
            "thread_id": thread_id,
            "protocol": protocol.name,
            "current_phase": current_phase,
            "passed": False,
            "error": "phase_not_found",
        }
        _emit_payload(payload, as_json=True)
        raise typer.Exit(2)

    phase_participants = thread.get("phase_participants", [])
    contributions = (
        thread_store.list_contributions(thread_id, phase=current_phase)
        if current_phase
        else []
    )
    if not isinstance(phase_participants, list):
        phase_participants = []
    return thread, protocol, phase, [str(name) for name in phase_participants if isinstance(name, str)], contributions


def _thread_chat_tldr(content: str, limit: int = 80) -> str:
    text = " ".join(content.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_live_message(
    sender: str,
    content: str,
    *,
    actor: str,
    targets: list[str] | None = None,
) -> str:
    clean_content = " ".join(content.split())
    if sender == actor:
        if targets:
            return f"you -> @{', @'.join(targets)}: {clean_content}"
        return f"you: {clean_content}"
    return f"{sender}: {clean_content}"


class _LiveStatusDisplay:
    """Render one transient interactive status line above the chat log."""

    _ACTIVE_STATES = {"queued", "received", "thinking", "working", "responding"}

    def __init__(self, *, console: object | None = None, enabled: bool = False):
        self.console = console if console is not None else globals()["console"]
        self.enabled = enabled
        self._lock = threading.Lock()
        self._visible = False

    def _write_control(self, value: str) -> bool:
        stream = getattr(self.console, "file", None)
        if stream is None or not hasattr(stream, "write"):
            return False
        stream.write(value)
        flush = getattr(stream, "flush", None)
        if callable(flush):
            flush()
        return True

    def show_agent_state(self, agent_name: str, state: str) -> bool:
        if not self.enabled or state not in self._ACTIVE_STATES:
            return False
        with self._lock:
            if not self._write_control(f"\r\x1b[2K{agent_name} is {state}..."):
                self.console.print(f"\r\x1b[2K{agent_name} is {state}...", end="")
            self._visible = True
        return True

    def clear(self) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if not self._visible:
                return False
            if not self._write_control("\r\x1b[2K"):
                self.console.print("\r\x1b[2K", end="")
            self._visible = False
        return True

    def print(self, value: object = "", *args, **kwargs) -> None:
        with self._lock:
            if self.enabled and self._visible:
                if not self._write_control("\r\x1b[2K"):
                    self.console.print("\r\x1b[2K", end="")
                self._visible = False
            self.console.print(value, *args, **kwargs)


def _format_live_thread_entry(entry: dict[str, object]) -> str:
    participants = ", ".join(str(name) for name in entry.get("participants", []))
    attached_agents = entry.get("attached_agents", [])
    attached_label = ", ".join(str(name) for name in attached_agents) if attached_agents else "-"
    return (
        f"{entry.get('thread_id')}  {entry.get('topic')}\n"
        f"  protocol: {entry.get('protocol')}  phase: {entry.get('current_phase')}  status: {entry.get('status')}\n"
        f"  participants: {participants}\n"
        f"  attached_agents: {attached_label}"
    )


def _list_live_threads(config: BTwinConfig | None = None) -> list[dict[str, object]]:
    current_config = config or _get_config()
    _require_attached_live(current_config)
    threads = _attached_api_get_or_exit("/api/threads", {"status": "active"})
    attached_by_thread = _attached_agents_by_thread()
    summaries: list[dict[str, object]] = []
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        thread_id = thread.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        participants = thread.get("participants", [])
        participant_names = [
            participant.get("name")
            for participant in participants
            if isinstance(participant, dict) and isinstance(participant.get("name"), str)
        ]
        summaries.append(
            {
                "thread_id": thread_id,
                "topic": thread.get("topic", ""),
                "protocol": thread.get("protocol", ""),
                "status": thread.get("status", ""),
                "current_phase": thread.get("current_phase", ""),
                "participants": participant_names,
                "attached_agents": attached_by_thread.get(thread_id, []),
            }
        )
    return summaries


def _load_thread_enter_snapshot(
    thread_id: str,
    actor: str,
    config: BTwinConfig | None = None,
) -> dict[str, object]:
    current_config = config or _get_config()
    thread, protocol, phase, _phase_participants, _contributions = _load_protocol_flow_context(thread_id, current_config)
    participant_names = sorted(_thread_participant_names(thread))
    if actor not in participant_names:
        console.print(f"[red]Agent is not a participant on this thread:[/red] {actor} not in {thread_id}")
        raise typer.Exit(4)

    if _use_attached_api(current_config):
        inbox_payload = _attached_api_get_or_exit(f"/api/threads/{thread_id}/inbox", {"agent": actor})
        pending_messages = inbox_payload.get("messages", []) if isinstance(inbox_payload, dict) else []
        pending_count = inbox_payload.get("pending_count", len(pending_messages)) if isinstance(inbox_payload, dict) else 0
        recent_messages: list[dict] = []
    else:
        store = _get_thread_store()
        pending_messages = store.list_inbox(thread_id, actor) or []
        pending_count = len(pending_messages)
        recent_messages = store.list_recent_messages(thread_id, limit=5)

    return {
        "thread_id": thread_id,
        "topic": thread.get("topic", ""),
        "protocol": protocol.name,
        "current_phase": phase.name,
        "participants": participant_names,
        "actor": actor,
        "interaction_mode": protocol.interaction.mode,
        "pending_count": pending_count,
        "pending_messages": pending_messages,
        "recent_messages": recent_messages,
    }


def _load_live_enter_snapshot(
    thread_id: str,
    actor: str,
    config: BTwinConfig | None = None,
) -> dict[str, object]:
    current_config = config or _get_config()
    _require_attached_live(current_config)
    thread, protocol, phase, _phase_participants, _contributions = _load_protocol_flow_context(thread_id, current_config)
    participant_names = sorted(_thread_participant_names(thread))
    if actor not in participant_names:
        console.print(f"[red]Actor is not a participant on this thread:[/red] {actor} not in {thread_id}")
        raise typer.Exit(4)

    inbox_payload = _attached_api_get_or_exit(f"/api/threads/{thread_id}/inbox", {"agent": actor})
    pending_messages = inbox_payload.get("messages", []) if isinstance(inbox_payload, dict) else []
    pending_count = inbox_payload.get("pending_count", len(pending_messages)) if isinstance(inbox_payload, dict) else 0
    recent_payload = _attached_api_get_or_exit(f"/api/threads/{thread_id}/messages")
    recent_messages = recent_payload[-5:] if isinstance(recent_payload, list) else []
    attached_agents = _attached_agents_by_thread().get(thread_id, [])
    return {
        "thread_id": thread_id,
        "topic": thread.get("topic", ""),
        "protocol": protocol.name,
        "current_phase": phase.name,
        "participants": participant_names,
        "actor": actor,
        "interaction_mode": protocol.interaction.mode,
        "pending_count": pending_count,
        "pending_messages": pending_messages,
        "recent_messages": recent_messages,
        "attached_agents": attached_agents,
    }


def _render_thread_enter_snapshot(snapshot: dict[str, object]) -> str:
    payload = {
        "thread_id": snapshot.get("thread_id"),
        "topic": snapshot.get("topic"),
        "protocol": snapshot.get("protocol"),
        "current_phase": snapshot.get("current_phase"),
        "actor": snapshot.get("actor"),
        "interaction_mode": snapshot.get("interaction_mode"),
        "participants": snapshot.get("participants"),
        "pending_count": snapshot.get("pending_count"),
    }
    return yaml.safe_dump(payload, sort_keys=False).strip()


def _render_live_enter_snapshot(snapshot: dict[str, object]) -> str:
    participants = ", ".join(str(name) for name in snapshot.get("participants", []))
    attached_agents = ", ".join(str(name) for name in snapshot.get("attached_agents", [])) or "-"
    lines = [
        f"live thread {snapshot.get('thread_id')}\n"
        f"topic: {snapshot.get('topic')}\n"
        f"protocol: {snapshot.get('protocol')}  phase: {snapshot.get('current_phase')}\n"
        f"actor: {snapshot.get('actor')}  interaction_mode: {snapshot.get('interaction_mode')}\n"
        f"participants: {participants}\n"
        f"attached_agents: {attached_agents}\n"
        f"pending_count: {snapshot.get('pending_count', 0)}"
    ]
    recent_messages = snapshot.get("recent_messages", [])
    if isinstance(recent_messages, list) and recent_messages:
        lines.append("recent:")
        actor = str(snapshot.get("actor") or "")
        for message in recent_messages:
            if not isinstance(message, dict):
                continue
            sender = message.get("from")
            if not isinstance(sender, str) or not sender:
                continue
            content = message.get("_content") or message.get("content") or message.get("tldr") or ""
            if not isinstance(content, str) or not content.strip():
                continue
            targets = message.get("target_agents") if isinstance(message.get("target_agents"), list) else []
            lines.append(f"  {_format_live_message(sender, content, actor=actor, targets=targets)}")
    return "\n".join(lines)


def _print_thread_enter_help() -> None:
    console.print("Commands: /help /status /inbox /exit")
    console.print("Prefixes: !message -> broadcast, @agent message -> direct")


def _print_live_enter_help() -> None:
    console.print("Commands: /help /status /inbox /exit")
    console.print("Prefixes: !message -> broadcast, @agent message -> direct")


def _thread_enter_send_message(
    thread_id: str,
    actor: str,
    decision,
    config: BTwinConfig | None = None,
) -> dict[str, object]:
    current_config = config or _get_config()
    content = decision.content.strip()
    if not content:
        raise typer.BadParameter("Message content is required.")

    if _use_attached_api(current_config):
        payload = {
            "fromAgent": actor,
            "content": content,
            "tldr": _thread_chat_tldr(content),
            "deliveryMode": decision.mode,
            "targetAgents": decision.targets,
        }
        return _attached_api_call_or_exit(f"/api/threads/{thread_id}/messages", payload)

    message = _get_thread_store().send_message(
        thread_id=thread_id,
        from_agent=actor,
        content=content,
        tldr=_thread_chat_tldr(content),
        delivery_mode=decision.mode or "broadcast",
        target_agents=decision.targets,
    )
    if message is None:
        console.print(f"[red]Thread not found or closed:[/red] {thread_id}")
        raise typer.Exit(4)
    return message


def _live_enter_send_message(
    thread_id: str,
    actor: str,
    decision,
    config: BTwinConfig | None = None,
) -> dict[str, object]:
    return _thread_enter_send_message(thread_id, actor, decision, config)


def _render_live_inbox_messages(
    thread_id: str,
    actor: str,
    *,
    seen_message_ids: set[str],
) -> int:
    payload = _attached_api_get_or_exit(f"/api/threads/{thread_id}/inbox", {"agent": actor})
    if not isinstance(payload, dict):
        return 0
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        return 0

    shown = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_id = message.get("message_id")
        if isinstance(message_id, str) and message_id in seen_message_ids:
            continue
        sender = message.get("from")
        if not isinstance(sender, str) or not sender:
            continue
        content = message.get("_content") or message.get("content") or message.get("tldr") or ""
        if not isinstance(content, str) or not content.strip():
            continue
        targets = message.get("target_agents") if isinstance(message.get("target_agents"), list) else []
        console.print(_format_live_message(sender, content, actor=actor, targets=targets))
        if isinstance(message_id, str) and message_id:
            seen_message_ids.add(message_id)
        shown += 1
    return shown


def _attached_event_stream():
    import httpx

    with httpx.Client(base_url=_api_base_url(), timeout=None) as client:
        with client.stream("GET", "/api/events") as response:
            response.raise_for_status()
            event_name: str | None = None
            data_lines: list[str] = []
            for raw_line in response.iter_lines():
                line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
                if not line:
                    if data_lines:
                        payload = json.loads("".join(data_lines))
                        if event_name and isinstance(payload, dict) and "type" not in payload:
                            payload["type"] = event_name
                        yield payload
                    event_name = None
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line.partition(":")[2].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line.partition(":")[2].lstrip())


def _start_live_event_listener(thread_id: str) -> queue_module.Queue[dict[str, object]]:
    events: queue_module.Queue[dict[str, object]] = queue_module.Queue()

    def worker() -> None:
        try:
            for event in _attached_event_stream():
                if not isinstance(event, dict):
                    continue
                resource_id = event.get("resource_id")
                if resource_id not in {thread_id, "active"}:
                    continue
                events.put(event)
        except Exception as exc:
            events.put({"type": "listener_error", "resource_id": thread_id, "error": str(exc)})

    thread = threading.Thread(target=worker, daemon=True, name=f"btwin-live-events-{thread_id}")
    thread.start()
    return events


def _render_live_event(
    event: dict[str, object],
    *,
    actor: str,
    seen_message_ids: set[str],
    status_display: _LiveStatusDisplay | None = None,
) -> int:
    event_type = event.get("type")
    if event_type == "listener_error":
        error = event.get("error")
        if isinstance(error, str) and error:
            if status_display is not None:
                status_display.print(f"[yellow]live event stream ended:[/yellow] {error}")
            else:
                console.print(f"[yellow]live event stream ended:[/yellow] {error}")
        return 1
    if event_type == "agent_session_state":
        agent_name = event.get("agent_name")
        state = event.get("state")
        if isinstance(agent_name, str) and isinstance(state, str) and state in {
            "queued",
            "thinking",
            "working",
            "received",
            "responding",
            "done",
            "failed",
            "fallback",
        }:
            if status_display is not None:
                if status_display.show_agent_state(agent_name, state):
                    return 1
                if state == "done":
                    status_display.clear()
                    return 1
                status_display.print(f"{agent_name} is {state}")
                return 1
            console.print(f"{agent_name} is {state}")
            return 1
        return 0
    if event_type == "message_sent":
        sender = event.get("from_agent")
        if not isinstance(sender, str) or sender == actor:
            return 0
        message_id = event.get("message_id")
        if isinstance(message_id, str) and message_id in seen_message_ids:
            return 0
        content = event.get("content")
        if not isinstance(content, str) or not content.strip():
            return 0
        targets = event.get("target_agents") if isinstance(event.get("target_agents"), list) else []
        formatted = _format_live_message(sender, content, actor=actor, targets=targets)
        if status_display is not None:
            status_display.print(formatted)
        else:
            console.print(formatted)
        if isinstance(message_id, str) and message_id:
            seen_message_ids.add(message_id)
        return 1
    return 0


def _render_live_events(
    events: queue_module.Queue[dict[str, object]],
    *,
    actor: str,
    seen_message_ids: set[str],
    wait_seconds: float = 0.0,
    status_display: _LiveStatusDisplay | None = None,
) -> int:
    rendered = 0

    if wait_seconds > 0:
        try:
            first = events.get(timeout=wait_seconds)
            rendered += _render_live_event(
                first,
                actor=actor,
                seen_message_ids=seen_message_ids,
                status_display=status_display,
            )
        except queue_module.Empty:
            return 0

    while True:
        try:
            rendered += _render_live_event(
                events.get_nowait(),
                actor=actor,
                seen_message_ids=seen_message_ids,
                status_display=status_display,
            )
        except queue_module.Empty:
            break
    return rendered


def _start_live_event_printer(
    events: queue_module.Queue[dict[str, object]],
    *,
    actor: str,
    seen_message_ids: set[str],
    status_display: _LiveStatusDisplay | None = None,
) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()

    def worker() -> None:
        while not stop_event.is_set():
            try:
                event = events.get(timeout=0.2)
            except queue_module.Empty:
                continue
            _render_live_event(
                event,
                actor=actor,
                seen_message_ids=seen_message_ids,
                status_display=status_display,
            )

    thread = threading.Thread(
        target=worker,
        daemon=True,
        name=f"btwin-live-printer-{actor}",
    )
    thread.start()
    return stop_event, thread


def _project_root() -> Path:
    """Resolve the active project root for local handoff snapshot writes."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return Path.cwd()

    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip())
    return Path.cwd()


def _is_valid_cron_schedule(value: str) -> bool:
    parts = value.strip().split()
    if len(parts) != 5:
        return False
    token_pattern = re.compile(r"^[0-9*/,\-]+$")
    return all(bool(token_pattern.match(part)) for part in parts)


def _atomic_write_yaml(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
    tmp_path.replace(path)


def _require_macos_service_support() -> None:
    if sys.platform != "darwin":
        console.print("[red]`btwin service` is only supported on macOS.[/red]")
        raise typer.Exit(1)


def _service_domain() -> str:
    return f"gui/{os.getuid()}"


def _service_target() -> str:
    return f"{_service_domain()}/{_SERVICE_LABEL}"


def _service_data_dir() -> Path:
    default_data_dir = Path.home() / ".btwin"
    if "BTWIN_CONFIG_PATH" not in os.environ:
        return default_data_dir
    return _get_active_data_dir()


def _service_plist_path() -> Path:
    return _service_data_dir() / f"{_SERVICE_LABEL}.plist"


def _service_link_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_SERVICE_LABEL}.plist"


def _service_logs_dir() -> Path:
    return _service_data_dir() / "logs"


def _resolve_btwin_executable() -> Path:
    resolved = shutil.which("btwin")
    if not resolved:
        console.print("[red]Could not find `btwin` executable in PATH.[/red]")
        raise typer.Exit(1)
    return Path(resolved).expanduser()


def _write_service_plist(btwin_executable: Path) -> Path:
    _service_logs_dir().mkdir(parents=True, exist_ok=True)
    plist_path = _service_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "Label": _SERVICE_LABEL,
        "ProgramArguments": [str(btwin_executable), "serve-api"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(_service_logs_dir() / "serve-api.stdout.log"),
        "StandardErrorPath": str(_service_logs_dir() / "serve-api.stderr.log"),
        "EnvironmentVariables": {
            "PATH": f"{Path.home() / '.local' / 'bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
    }
    plist_path.write_bytes(plistlib.dumps(payload, sort_keys=False))
    return plist_path


def _ensure_service_link(plist_path: Path) -> Path:
    link_path = _service_link_path()
    link_path.parent.mkdir(parents=True, exist_ok=True)

    if link_path.is_symlink() or link_path.exists():
        if link_path.is_dir() and not link_path.is_symlink():
            console.print(f"[red]LaunchAgent path is a directory:[/red] {link_path}")
            raise typer.Exit(1)
        link_path.unlink()

    link_path.symlink_to(plist_path)
    return link_path


def _run_service_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=check)
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            console.print(exc.stdout.rstrip())
        if exc.stderr:
            console.print(f"[red]{exc.stderr.rstrip()}[/red]")
        raise typer.Exit(exc.returncode or 1)


def _detect_project_name() -> str:
    """Auto-detect project name from git remote or current directory name."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            url = result.stdout.strip()
            # Handle both HTTPS and SSH URLs:
            #   https://github.com/user/repo.git  ->  repo
            #   git@github.com:user/repo.git      ->  repo
            name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            if name.endswith(".git"):
                name = name[:-4]
            if name:
                return name
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return Path.cwd().name


def _register_claude_global(force: bool = False) -> bool:
    """Register btwin in Claude Code global config (~/.claude.json)."""
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        return False

    try:
        data = json.loads(claude_json.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    servers = data.get("mcpServers", {})
    if "btwin" in servers and not force:
        console.print("[dim]  Claude Code: already registered[/dim]")
        return True

    servers["btwin"] = {
        "type": "stdio",
        "command": "btwin",
        "args": ["mcp-proxy"],
        "env": {},
    }
    data["mcpServers"] = servers
    claude_json.write_text(json.dumps(data, indent=2) + "\n")
    console.print("[green]  Claude Code: registered globally[/green]")
    return True


def _register_codex_global(force: bool = False) -> bool:
    """Register btwin in Codex global config (~/.codex/config.toml)."""
    codex_dir = Path.home() / ".codex"
    codex_config = codex_dir / "config.toml"

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    existing: dict = {}
    if codex_config.exists():
        try:
            existing = tomllib.loads(codex_config.read_text())
        except Exception:
            pass

    servers = existing.get("mcp_servers", {})
    if "btwin" in servers and not force:
        console.print("[dim]  Codex: already registered[/dim]")
        return True

    codex_dir.mkdir(parents=True, exist_ok=True)

    # Build TOML content preserving existing config
    lines: list[str] = []
    if codex_config.exists():
        raw = codex_config.read_text()
        # Remove existing btwin section if force
        if force:
            import re
            raw = re.sub(
                r'\[mcp_servers\.btwin\]\n(?:[^\[]*\n)*',
                '',
                raw,
            )
        lines.append(raw.rstrip())

    lines.append("")
    lines.append("[mcp_servers.btwin]")
    lines.append('command = "btwin"')
    lines.append('args = ["mcp-proxy"]')
    lines.append("")

    codex_config.write_text("\n".join(lines))
    console.print("[green]  Codex: registered globally[/green]")
    return True


def _write_codex_project_config(config_path: Path, project_name: str) -> None:
    """Write project-scoped Codex MCP config for btwin."""
    config_path.parent.mkdir(parents=True, exist_ok=True)

    raw = ""
    if config_path.exists():
        raw = config_path.read_text()
        raw = re.sub(
            r'\[mcp_servers\.btwin\]\n(?:[^\[]*\n)*',
            "",
            raw,
        ).rstrip()

    lines: list[str] = []
    if raw:
        lines.append(raw)

    lines.append("[mcp_servers.btwin]")
    lines.append('command = "btwin"')
    lines.append(f'args = ["mcp-proxy", "--project", "{project_name}"]')
    lines.append("")

    config_path.write_text("\n".join(lines))


@agent_app.command("list")
def agent_list(
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List registered agent definitions."""
    agents = _get_agent_store().list_agents()
    _emit_payload(agents, as_json=as_json)


@agent_app.command("show")
def agent_show(
    name: str = typer.Argument(..., help="Agent name"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show one registered agent definition."""
    agent = _get_agent_store().get_agent(name)
    if agent is None:
        console.print(f"[red]Agent not found:[/red] {name}")
        raise typer.Exit(4)
    _emit_payload(agent, as_json=as_json)


@agent_app.command("create")
def agent_create(
    name: str = typer.Argument(..., help="Agent name"),
    provider: str = typer.Option(..., "--provider", help="Provider name"),
    role: str = typer.Option(..., "--role", help="Agent role"),
    model: str = typer.Option(..., "--model", help="Model name"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Create a registered agent definition."""
    agent = _get_agent_store().register(
        name=name,
        model=model,
        alias=name,
        provider=provider,
        role=role,
    )
    _emit_payload(agent, as_json=as_json)


@agent_app.command("inbox")
def agent_inbox(
    name: str = typer.Argument(..., help="Agent name"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Summarize what one agent should look at next."""
    agent = _get_agent_store().get_agent(name)
    if agent is None:
        console.print(f"[red]Agent not found:[/red] {name}")
        raise typer.Exit(4)

    config = _get_config()
    agent_store = _get_agent_store()
    queue_root = agent_store.data_dir
    queue = _build_agent_queue_summary(name, queue_root, agent_store)
    thread_store = None if _use_attached_api(config) else _get_thread_store()
    active_threads, thread_summary_warning = _build_agent_thread_summary(
        name,
        thread_store=thread_store,
        config=config,
    )
    runtime_sessions, runtime_warning, runtime_error = _get_attached_runtime_sessions(name, config)
    thread_data_dir = (config.data_dir / "threads") if _use_attached_api(config) else thread_store.data_dir
    attached_runtime_diagnostics = (
        _attached_runtime_diagnostics_context() if _use_attached_api(config) and as_json else None
    )

    pending_thread_count = sum(1 for thread in active_threads if thread["pending_message_count"] > 0)
    pending_message_count = sum(thread["pending_message_count"] for thread in active_threads)

    payload = {
        "agent": agent,
        "context": {
            "agent_data_dir": str(agent_store.data_dir),
            "workflow_data_dir": str(queue_root),
            "thread_data_dir": str(thread_data_dir),
            "config_data_dir": str(config.data_dir),
            "project_root": str(_project_root()),
            "runtime_mode": config.runtime.mode,
        },
        "queue_count": len(queue),
        "queue": queue,
        "active_thread_count": len(active_threads),
        "active_threads": active_threads,
        "pending_thread_count": pending_thread_count,
        "pending_message_count": pending_message_count,
        "runtime_session_count": len(runtime_sessions),
        "runtime_sessions": runtime_sessions,
        "runtime_session_warning": runtime_warning,
        "runtime_session_error": runtime_error,
        "thread_summary_warning": thread_summary_warning,
    }
    if attached_runtime_diagnostics is not None:
        payload["attached_runtime_diagnostics"] = attached_runtime_diagnostics
    _emit_payload(payload, as_json=as_json)


@protocol_app.command("list")
def protocol_list(
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List available protocol definitions."""
    protocols = _get_protocol_store().list_protocols()
    _emit_payload(protocols, as_json=as_json)


@protocol_app.command("show")
def protocol_show(
    name: str = typer.Argument(..., help="Protocol name"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show one protocol definition."""
    protocol = _get_protocol_store().get_protocol(name)
    if protocol is None:
        console.print(f"[red]Protocol not found:[/red] {name}")
        raise typer.Exit(4)
    _emit_payload(protocol.model_dump(exclude_none=True), as_json=as_json)


@protocol_app.command("validate")
def protocol_validate(
    file: str = typer.Option(..., "--file", help="Path to a protocol YAML file"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Validate a protocol YAML file."""
    path = Path(file).expanduser()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        protocol = Protocol.model_validate(data)
    except Exception as exc:
        payload = {"valid": False, "file": str(path), "error": str(exc)}
        _emit_payload(payload, as_json=as_json)
        raise typer.Exit(2)

    payload = {
        "valid": True,
        "file": str(path),
        "name": protocol.name,
        "description": protocol.description,
        "phase_count": len(protocol.phases),
    }
    _emit_payload(payload, as_json=as_json)


@protocol_app.command("check")
def protocol_check(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Validate the current thread phase against protocol contribution requirements."""
    thread, protocol, phase, phase_participants, contributions = _load_protocol_flow_context(thread_id, _get_config())
    current_phase = thread.get("current_phase")

    validation = ProtocolValidator.validate_phase(
        phase_participants=phase_participants,
        template_sections=phase.template or [],
        contributions=contributions,
    )
    payload = {
        "thread_id": thread_id,
        "protocol": protocol.name,
        "current_phase": current_phase,
        "phase_actions": list(phase.actions),
        "phase_participants": phase_participants,
        "passed": validation.passed,
        "missing": validation.missing,
    }
    _emit_payload(payload, as_json=as_json)


@protocol_app.command("next")
def protocol_next(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    outcome: str | None = typer.Option(None, "--outcome", help="Protocol outcome to apply"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Calculate the next valid protocol action from the current thread state."""
    thread, protocol, phase, _phase_participants, contributions = _load_protocol_flow_context(thread_id, _get_config())
    plan = describe_next(thread, protocol, contributions, outcome=outcome)
    payload = plan.model_dump(exclude={"manual_outcome_required"})
    _emit_payload(payload, as_json=as_json)
    if plan.error:
        raise typer.Exit(2)


@protocol_app.command("apply-next")
def protocol_apply_next(
    thread_id: str | None = typer.Option(None, "--thread", help="Thread id"),
    outcome: str | None = typer.Option(None, "--outcome", help="Protocol outcome to apply"),
    summary: str | None = typer.Option(None, "--summary", help="Thread summary for close actions"),
    decision: str | None = typer.Option(None, "--decision", help="Thread decision for close actions"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Apply the next protocol action when it is unambiguous."""
    config = _get_config()
    resolved_thread_id, thread_source = _resolve_runtime_thread_id(thread_id, config)
    thread, protocol, phase, _phase_participants, contributions = _load_protocol_flow_context(resolved_thread_id, config)
    plan = describe_next(thread, protocol, contributions, outcome=outcome)
    base_payload = {
        "thread_id": resolved_thread_id,
        "thread_source": thread_source,
        "protocol": protocol.name,
        "current_phase": plan.current_phase,
        "passed": plan.passed,
        "missing": plan.missing,
        "valid_outcomes": plan.valid_outcomes,
        "requested_outcome": plan.requested_outcome,
        "next_phase": plan.next_phase,
        "suggested_action": plan.suggested_action,
        "applied": False,
    }

    if plan.error:
        base_payload["error"] = plan.error
        _emit_payload(base_payload, as_json=as_json)
        raise typer.Exit(2)

    if not plan.passed:
        base_payload["manual_outcome_required"] = False
        _emit_payload(base_payload, as_json=as_json)
        raise typer.Exit(0)

    if plan.suggested_action == "record_outcome":
        base_payload["manual_outcome_required"] = True
        _emit_payload(base_payload, as_json=as_json)
        raise typer.Exit(0)

    if plan.suggested_action == "close_thread":
        if summary is None:
            base_payload["summary_required"] = True
            _emit_payload(base_payload, as_json=as_json)
            raise typer.Exit(0)

        if _use_attached_api(config):
            payload: dict[str, object] = {"summary": summary}
            if decision is not None:
                payload["decision"] = decision
            closed = _attached_api_call_or_exit(f"/api/threads/{resolved_thread_id}/close", payload)
        else:
            store = _get_thread_store()
            closed = store.close_thread(resolved_thread_id, summary=summary, decision=decision)
            if closed is None:
                console.print(f"[red]Thread not found:[/red] {resolved_thread_id}")
                raise typer.Exit(4)
            result_record_id = _record_thread_result_entry(store.data_dir, resolved_thread_id, closed, summary, decision)
            if result_record_id:
                closed = dict(closed)
                closed["result_record_id"] = result_record_id

        result_payload = {
            "thread_id": resolved_thread_id,
            "thread_source": thread_source,
            "protocol": protocol.name,
            "current_phase": plan.current_phase,
            "next_phase": plan.next_phase,
            "suggested_action": plan.suggested_action,
            "applied": True,
            "thread": closed,
        }
        _emit_payload(result_payload, as_json=as_json)
        return

    if plan.suggested_action == "advance_phase":
        if plan.next_phase is None:
            base_payload["error"] = "next_phase_unavailable"
            _emit_payload(base_payload, as_json=as_json)
            raise typer.Exit(2)

        if _use_attached_api(config):
            closed_or_updated = _attached_api_call_or_exit(
                f"/api/threads/{resolved_thread_id}/advance-phase",
                {"nextPhase": plan.next_phase},
            )
        else:
            store = _get_thread_store()
            closed_or_updated = store.advance_phase(resolved_thread_id, next_phase=plan.next_phase)
            if closed_or_updated is None:
                console.print(f"[red]Thread not found:[/red] {resolved_thread_id}")
                raise typer.Exit(4)

        result_payload = {
            "thread_id": resolved_thread_id,
            "thread_source": thread_source,
            "protocol": protocol.name,
            "current_phase": plan.current_phase,
            "next_phase": plan.next_phase,
            "suggested_action": plan.suggested_action,
            "applied": True,
            "thread": closed_or_updated,
        }
        _emit_payload(result_payload, as_json=as_json)
        return

    base_payload["manual_outcome_required"] = True
    _emit_payload(base_payload, as_json=as_json)


@thread_app.command("create")
def thread_create(
    topic: str = typer.Option(..., "--topic", help="Thread topic"),
    protocol: str = typer.Option(..., "--protocol", help="Protocol name"),
    participant: list[str] = typer.Option([], "--participant", help="Participant agent name"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Create a new collaboration thread."""
    config = _get_config()
    if _use_attached_api(config):
        payload = {"topic": topic, "protocol": protocol}
        if participant:
            payload["participants"] = participant
        thread = _attached_api_call_or_exit("/api/threads", payload)
    else:
        protocol_store = _get_protocol_store()
        proto = protocol_store.get_protocol(protocol)
        if proto is None:
            console.print(f"[red]Protocol not found:[/red] {protocol}")
            raise typer.Exit(4)

        store = _get_thread_store()
        locale = LocaleSettingsStore(store.data_dir).read().model_dump()
        thread = store.create_thread(
            topic=topic,
            protocol=protocol,
            participants=participant or None,
            initial_phase=proto.phases[0].name if proto.phases else None,
            locale=locale,
        )
    payload = _thread_create_payload(thread)
    if as_json:
        _emit_payload(payload, as_json=True)
        return

    enter_command = payload.pop("enter_command", None)
    _emit_payload(payload, as_json=False)
    if isinstance(enter_command, str):
        console.print("")
        console.print("Join this thread:")
        console.print(f"  {enter_command}")


@live_app.command("threads")
def live_threads(
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List active threads in attached live mode."""
    config = _get_config()
    _require_attached_live(config)
    threads = _list_live_threads(config)
    if as_json:
        _emit_payload(threads, as_json=True)
        return
    if not threads:
        console.print("[dim]No live threads found.[/dim]")
        return
    for index, thread in enumerate(threads):
        if index:
            console.print("")
        console.print(_format_live_thread_entry(thread))


@live_app.command("status")
def live_status(
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show attached live runtime status summary."""
    config = _get_config()
    _require_attached_live(config)
    threads = _list_live_threads(config)
    runtime_payload = _attached_runtime_sessions_payload()
    agents_payload = runtime_payload.get("agents", {}) if isinstance(runtime_payload, dict) else {}
    agent_names = sorted(name for name in agents_payload if isinstance(name, str))
    payload = {
        "url": _api_base_url(),
        "thread_count": len(threads),
        "attached_agent_count": len(agent_names),
        "attached_agents": agent_names,
        "threads": threads,
    }
    if as_json:
        _emit_payload(payload, as_json=True)
        return
    console.print(f"live api: {_api_base_url()}")
    console.print(f"thread_count: {len(threads)}")
    console.print(f"attached_agent_count: {len(agent_names)}")
    console.print(f"attached_agents: {', '.join(agent_names) if agent_names else '-'}")


@live_app.command("attach")
def live_attach(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    agent_name: str = typer.Option(..., "--agent", help="Agent name"),
    full_auto: bool = typer.Option(
        True,
        "--full-auto/--no-full-auto",
        help="Allow the attached helper agent to run without interactive approval prompts.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Attach one agent to a live thread."""
    config = _get_config()
    _require_attached_live(config)
    payload = _attached_api_call_or_exit(
        f"/api/threads/{thread_id}/spawn-agent",
        {"agentName": agent_name, "bypassPermissions": full_auto},
    )
    if as_json:
        _emit_payload(payload, as_json=True)
        return
    mode = "full-auto" if full_auto else "approval-required"
    console.print(f"attached {agent_name} -> {thread_id} ({mode})")


@live_app.command("close")
def live_close(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    summary: str = typer.Option(..., "--summary", help="Thread summary"),
    decision: str | None = typer.Option(None, "--decision", help="Thread decision"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Close one live thread."""
    config = _get_config()
    _require_attached_live(config)
    payload: dict[str, object] = {"summary": summary}
    if decision is not None:
        payload["decision"] = decision
    closed = _attached_api_call_or_exit(f"/api/threads/{thread_id}/close", payload)
    _emit_payload(closed, as_json=as_json)


@live_app.command("enter")
def live_enter(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    actor: str = typer.Option(..., "--as", help="Actor name"),
    attach_agents: list[str] = typer.Option([], "--attach", help="Attach an agent before entering"),
    full_auto: bool = typer.Option(
        True,
        "--full-auto/--no-full-auto",
        help="When using --attach, allow the attached helper agents to run without interactive approval prompts.",
    ),
):
    """Enter an attached live thread with human-readable chat formatting."""
    config = _get_config()
    _require_attached_live(config)
    for agent_name in attach_agents:
        _attached_api_call_or_exit(
            f"/api/threads/{thread_id}/spawn-agent",
            {"agentName": agent_name, "bypassPermissions": full_auto},
        )

    snapshot = _load_live_enter_snapshot(thread_id, actor, config)
    events = _start_live_event_listener(thread_id)
    seen_message_ids: set[str] = set()
    stdin = typer.get_text_stream("stdin")
    interactive_stdin = bool(getattr(stdin, "isatty", lambda: False)())
    status_display = _LiveStatusDisplay(enabled=interactive_stdin)
    stop_printer: threading.Event | None = None
    printer_thread: threading.Thread | None = None
    if interactive_stdin:
        stop_printer, printer_thread = _start_live_event_printer(
            events,
            actor=actor,
            seen_message_ids=seen_message_ids,
            status_display=status_display,
        )

    try:
        status_display.clear()
        console.print(_render_live_enter_snapshot(snapshot))
        status_display.clear()
        _print_live_enter_help()
        status_display.clear()
        _render_live_inbox_messages(thread_id, actor, seen_message_ids=seen_message_ids)

        while True:
            raw = stdin.readline()
            if raw == "":
                break

            decision = parse_thread_chat_input(raw.rstrip("\n"))
            if decision.kind == "empty":
                continue

            if decision.kind == "command":
                command = decision.command or "help"
                if command == "exit":
                    break
                if command == "help":
                    status_display.clear()
                    _print_live_enter_help()
                    continue
                if command == "status":
                    snapshot = _load_live_enter_snapshot(thread_id, actor, config)
                    status_display.clear()
                    console.print(_render_live_enter_snapshot(snapshot))
                    continue
                if command == "inbox":
                    status_display.clear()
                    if _render_live_inbox_messages(thread_id, actor, seen_message_ids=seen_message_ids) == 0:
                        console.print("[dim]No new messages.[/dim]")
                    continue
                status_display.clear()
                console.print(f"[yellow]Unknown command:[/yellow] /{command}")
                continue

            status_display.clear()
            console.print(_format_live_message(actor, decision.content, actor=actor, targets=decision.targets))
            message = _live_enter_send_message(thread_id, actor, decision, config)
            message_id = message.get("message_id")
            if isinstance(message_id, str) and message_id:
                seen_message_ids.add(message_id)
            if not interactive_stdin:
                _render_live_events(
                    events,
                    actor=actor,
                    seen_message_ids=seen_message_ids,
                    wait_seconds=8.0,
                    status_display=status_display,
                )
    finally:
        if stop_printer is not None:
            stop_printer.set()
        if printer_thread is not None:
            printer_thread.join(timeout=0.5)
        status_display.clear()


@thread_app.command("enter")
def thread_enter(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    actor: str = typer.Option(..., "--as", help="Actor name"),
):
    """Enter a lightweight chat loop for one thread participant."""
    config = _get_config()
    snapshot = _load_thread_enter_snapshot(thread_id, actor, config)
    console.print(_render_thread_enter_snapshot(snapshot))
    _print_thread_enter_help()

    stdin = typer.get_text_stream("stdin")
    while True:
        raw = stdin.readline()
        if raw == "":
            break

        decision = parse_thread_chat_input(raw.rstrip("\n"))
        if decision.kind == "empty":
            continue

        if decision.kind == "command":
            command = decision.command or "help"
            if command == "exit":
                break
            if command == "help":
                _print_thread_enter_help()
                continue
            if command == "status":
                snapshot = _load_thread_enter_snapshot(thread_id, actor, config)
                console.print(_render_thread_enter_snapshot(snapshot))
                continue
            if command == "inbox":
                snapshot = _load_thread_enter_snapshot(thread_id, actor, config)
                inbox_payload = {
                    "thread_id": thread_id,
                    "agent": actor,
                    "pending_count": snapshot.get("pending_count", 0),
                    "messages": snapshot.get("pending_messages", []),
                }
                console.print(yaml.safe_dump(inbox_payload, sort_keys=False).strip())
                continue
            console.print(f"[yellow]Unknown command:[/yellow] /{command}")
            continue

        message = _thread_enter_send_message(thread_id, actor, decision, config)
        route = message.get("delivery_mode", decision.mode)
        console.print(f"route: {route}")
        targets = message.get("target_agents", [])
        if isinstance(targets, list) and targets:
            console.print(f"targets: {', '.join(str(target) for target in targets)}")
        message_id = message.get("message_id")
        if isinstance(message_id, str) and message_id:
            console.print(f"message_id: {message_id}")


@thread_app.command("list")
def thread_list(
    status: str | None = typer.Option(None, "--status", help="Filter by thread status"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List collaboration threads."""
    if status is not None and status not in {"active", "completed"}:
        raise typer.BadParameter("--status must be active or completed")

    config = _get_config()
    if _use_attached_api(config):
        threads = _attached_api_get_or_exit("/api/threads", {"status": status} if status else None)
    else:
        threads = _get_thread_store().list_threads(status=status)
    _emit_payload(threads, as_json=as_json)


@thread_app.command("close")
def thread_close(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    summary: str = typer.Option(..., "--summary", help="Thread summary"),
    decision: str | None = typer.Option(None, "--decision", help="Thread decision"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Close a collaboration thread."""
    config = _get_config()
    if _use_attached_api(config):
        payload: dict[str, object] = {"summary": summary}
        if decision is not None:
            payload["decision"] = decision
        closed = _attached_api_call_or_exit(f"/api/threads/{thread_id}/close", payload)
    else:
        store = _get_thread_store()
        closed = store.close_thread(thread_id, summary=summary, decision=decision)
        if closed is None:
            console.print(f"[red]Thread not found:[/red] {thread_id}")
            raise typer.Exit(4)

        result_record_id = _record_thread_result_entry(
            store.data_dir,
            thread_id,
            closed,
            summary,
            decision,
        )
        if result_record_id:
            closed = dict(closed)
            closed["result_record_id"] = result_record_id
    _emit_payload(closed, as_json=as_json)


@thread_app.command("show")
def thread_show(
    thread_id: str = typer.Argument(..., help="Thread id"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show one thread and its current status summary."""
    config = _get_config()
    if _use_attached_api(config):
        thread = _attached_api_get_or_exit(f"/api/threads/{thread_id}")
        status_summary = _attached_api_get_or_exit(f"/api/threads/{thread_id}/status")
    else:
        store = _get_thread_store()
        thread = store.get_thread(thread_id)
        if thread is None:
            console.print(f"[red]Thread not found:[/red] {thread_id}")
            raise typer.Exit(4)
        status_summary = store.get_status(thread_id)

    payload = dict(thread)
    payload["status_summary"] = status_summary
    _emit_payload(payload, as_json=as_json)


@thread_app.command("send-message")
def thread_send_message(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    from_agent: str = typer.Option(..., "--from", help="Sender agent name"),
    content: str | None = typer.Option(None, "--content", help="Message content"),
    tldr: str = typer.Option(..., "--tldr", help="One-line message summary"),
    delivery_mode: str = typer.Option("auto", "--delivery-mode", help="broadcast, direct, or auto"),
    target_agents: list[str] = typer.Option([], "--target", help="Target agent for direct messages"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Persist a thread message with explicit delivery metadata."""
    if delivery_mode == "direct" and not target_agents:
        raise typer.BadParameter("--target is required when --delivery-mode=direct")

    resolved_content = _resolve_content(content)
    config = _get_config()
    if _use_attached_api(config):
        payload: dict[str, object] = {
            "fromAgent": from_agent,
            "content": resolved_content,
            "tldr": tldr,
            "deliveryMode": delivery_mode,
            "targetAgents": target_agents,
        }
        message = _attached_api_call_or_exit(f"/api/threads/{thread_id}/messages", payload)
    else:
        message = _get_thread_store().send_message(
            thread_id=thread_id,
            from_agent=from_agent,
            content=resolved_content,
            tldr=tldr,
            delivery_mode=delivery_mode,
            target_agents=target_agents,
        )
        if message is None:
            console.print(f"[red]Thread not found or closed:[/red] {thread_id}")
            raise typer.Exit(4)
    _emit_payload(message, as_json=as_json)


@thread_app.command("inbox")
def thread_inbox(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    agent_name: str = typer.Option(..., "--agent", help="Agent name"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Return pending messages relevant to one agent in a thread."""
    config = _get_config()
    if _use_attached_api(config):
        payload = _attached_api_get_or_exit(f"/api/threads/{thread_id}/inbox", {"agent": agent_name})
    else:
        messages = _get_thread_store().list_inbox(thread_id, agent_name)
        if messages is None:
            console.print(f"[red]Thread or participant not found:[/red] {thread_id} / {agent_name}")
            raise typer.Exit(4)

        payload = {
            "thread_id": thread_id,
            "agent": agent_name,
            "pending_count": len(messages),
            "messages": messages,
        }
    _emit_payload(payload, as_json=as_json)


@thread_app.command("status")
def thread_status(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    agent_name: str = typer.Option(..., "--agent", help="Agent name"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Return compact thread status for one participating agent."""
    config = _get_config()
    if _use_attached_api(config):
        payload = _attached_api_get_or_exit(f"/api/threads/{thread_id}/status", {"agent": agent_name})
    else:
        payload = _get_thread_store().get_agent_status(thread_id, agent_name)
        if payload is None:
            console.print(f"[red]Thread or participant not found:[/red] {thread_id} / {agent_name}")
            raise typer.Exit(4)
    _emit_payload(payload, as_json=as_json)


@thread_app.command("ack-message")
def thread_ack_message(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    message_id: str = typer.Option(..., "--message", help="Message id"),
    agent_name: str = typer.Option(..., "--agent", help="Agent acknowledging the message"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Acknowledge one message for one agent."""
    acked = _get_thread_store().ack_message(thread_id, message_id, agent_name)
    if not acked:
        console.print(f"[red]Thread or message not found:[/red] {thread_id} / {message_id}")
        raise typer.Exit(4)
    _emit_payload(
        {
            "thread_id": thread_id,
            "message_id": message_id,
            "agent": agent_name,
            "acked": True,
        },
        as_json=as_json,
    )


@contribution_app.command("submit")
def contribution_submit(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    agent_name: str = typer.Option(..., "--agent", help="Contributing agent name"),
    phase: str = typer.Option(..., "--phase", help="Protocol phase"),
    content: str | None = typer.Option(None, "--content", help="Contribution markdown"),
    tldr: str = typer.Option(..., "--tldr", help="One-line contribution summary"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Persist a structured contribution for the current protocol phase."""
    contribution = _get_thread_store().submit_contribution(
        thread_id=thread_id,
        agent_name=agent_name,
        phase=phase,
        content=_resolve_content(content),
        tldr=tldr,
    )
    if contribution is None:
        console.print(f"[red]Thread not found or closed:[/red] {thread_id}")
        raise typer.Exit(4)
    _emit_payload(contribution, as_json=as_json)


@service_app.command("install")
def service_install():
    """Install or refresh the macOS LaunchAgent for btwin serve-api."""
    _require_macos_service_support()

    btwin_executable = _resolve_btwin_executable()
    plist_path = _write_service_plist(btwin_executable)
    link_path = _ensure_service_link(plist_path)

    _run_service_command(["launchctl", "bootout", _service_target()], check=False)
    _run_service_command(["launchctl", "bootstrap", _service_domain(), str(link_path)])

    console.print("[green]B-TWIN service installed.[/green]")
    console.print(f"Plist: {plist_path}")
    console.print(f"LaunchAgent: {link_path}")
    console.print(f"Logs: {_service_logs_dir()}")


@service_app.command("status")
def service_status():
    """Show launchd status for the B-TWIN API service."""
    _require_macos_service_support()
    result = _run_service_command(["launchctl", "print", _service_target()])
    output = (result.stdout or result.stderr).strip()
    if output:
        console.print(output)


@service_app.command("restart")
def service_restart():
    """Restart the B-TWIN API service through launchd."""
    _require_macos_service_support()
    _run_service_command(["launchctl", "kickstart", "-k", _service_target()])
    console.print("[green]B-TWIN service restarted.[/green]")


@service_app.command("stop")
def service_stop():
    """Stop the B-TWIN API service through launchd."""
    _require_macos_service_support()
    result = _run_service_command(["launchctl", "bootout", _service_target()], check=False)
    if result.returncode == 0:
        console.print("[green]B-TWIN service stopped.[/green]")
        return

    output = (result.stderr or result.stdout).strip()
    if output:
        console.print(output)
    console.print("[yellow]B-TWIN service was not running.[/yellow]")


@app.command()
def init(
    project_name: str = typer.Argument(None, help="Project name (only for --local mode)"),
    provider: str = typer.Option("codex", "--provider", help="Provider to initialize"),
    local: bool = typer.Option(
        False,
        "--local",
        help="Create project-level provider config instead of global registration",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
):
    """Run initial B-TWIN provider setup.

    Today only Codex is supported. The provider flag remains explicit so future
    provider-specific initializers can plug into the same surface.
    """
    provider_name = _validate_init_provider(provider)
    provider_label = provider_display_name(provider_name)
    active_data_dir = _get_active_data_dir()
    providers_config_path = _providers_config_path(active_data_dir)

    console.print("[bold]B-TWIN Init[/bold]\n")
    console.print(f"[bold]Provider:[/bold] {provider_label}")
    console.print(f"[bold]Data dir:[/bold] {active_data_dir}\n")

    try:
        cli_path = validate_provider_cli(provider_name)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if providers_config_path.exists() and not force:
        provider_payload = json.loads(providers_config_path.read_text(encoding="utf-8"))
        console.print(f"[yellow]Reusing existing provider config[/yellow]")
        console.print(f"[dim]- Config: {providers_config_path}[/dim]\n")
    else:
        provider_payload = build_provider_config(provider_name)
        write_provider_config(providers_config_path, provider_payload)
        console.print(f"[green]Created provider config[/green] ({provider_label})")
        console.print(f"[dim]- CLI: {cli_path}[/dim]")
        console.print(f"[dim]- Config: {providers_config_path}[/dim]")
        console.print(
            f"[dim]- Models: {', '.join(model['id'] for model in provider_payload['providers'][0]['models'])}[/dim]\n"
        )

    if local:
        if project_name is None:
            project_name = _detect_project_name()

        codex_config_path = Path.cwd() / ".codex" / "config.toml"
        existing_paths = [path for path in (codex_config_path,) if path.exists()]

        if existing_paths and not force:
            existing_list = "\n".join(f"- {path.name if path.parent == Path.cwd() else path.relative_to(Path.cwd())}" for path in existing_paths)
            console.print(
                f"[yellow]Local {provider_label} MCP config already exists[/yellow]\n"
                f"{existing_list}\n"
                "Use [bold]--force[/bold] to overwrite."
            )
            raise typer.Exit(1)

        _write_codex_project_config(codex_config_path, project_name)
        console.print(f"[green]Created local {provider_label} MCP config[/green] (project: {project_name})")
        console.print("[dim]- Codex: .codex/config.toml[/dim]")
    else:
        console.print(f"[bold]Registering B-TWIN MCP for {provider_label}...[/bold]\n")
        _register_codex_global(force=force)

    console.print(
        "\n[bold]Next steps:[/bold]\n"
        "  1. btwin serve-api\n"
        "  2. Restart your MCP client session\n"
        "  3. btwin install-skills --platform codex\n"
    )


@app.command("mcp-proxy")
def mcp_proxy(
    project: str = typer.Option(None, help="Project name (auto-detected if omitted)"),
    backend: str = typer.Option("http://localhost:8787", help="Backend API URL"),
):
    """Start B-TWIN MCP proxy server (stdio transport).

    Lightweight proxy that forwards MCP tool calls to the B-TWIN HTTP API
    with automatic project injection.
    """
    from rich.console import Console as _ErrConsole

    resolved_project = project or _detect_project_name()
    _ErrConsole(stderr=True).print(
        f"[bold]Starting B-TWIN MCP Proxy: project={resolved_project} backend={backend}[/bold]"
    )
    from btwin_cli.doc_sync import sync_global_docs
    sync_global_docs(_get_active_data_dir())

    from btwin_cli import mcp_proxy as proxy

    proxy._project = resolved_project
    proxy._backend = backend
    proxy.configure_runtime(_get_active_data_dir())
    proxy.mcp.run(transport="stdio")


@app.command()
def setup():
    """Initialize B-TWIN data directory and config."""
    config_path = _config_path()
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    console.print("[bold]B-TWIN Setup[/bold]\n")

    config_data: dict[str, object] = {
        "llm": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        "session": {"timeout_minutes": 10},
        "promotion": {"enabled": True, "schedule": "0 9,21 * * *"},
        "data_dir": str(_get_active_data_dir()),
    }

    _atomic_write_yaml(config_path, config_data)

    console.print(f"[green]Config saved to {config_path}[/green]\n")
    console.print(
        "Next steps:\n"
        "  1. [bold]btwin init[/bold]                       — Create provider config and register btwin mcp-proxy\n"
        "  2. [bold]btwin serve-api[/bold]                  — Start the HTTP API on http://127.0.0.1:8787\n"
        "  3. [bold]btwin install-skills --platform codex[/bold] — Install B-TWIN Skills for your client\n"
        "  4. [bold]btwin search[/bold] <query>             — Search past entries\n"
        "  5. [bold]btwin serve[/bold]                      — Start the stdio MCP entrypoint via the shared HTTP proxy path\n"
    )


@app.command()
def serve():
    """Start the B-TWIN MCP server (stdio transport)."""
    import sys
    from rich.console import Console as _ErrConsole
    _ErrConsole(stderr=True).print(
        "[bold]Starting B-TWIN MCP server via HTTP proxy...[/bold]\n"
        "[dim]This path uses the shared serve-api backend to avoid index divergence.[/dim]"
    )
    mcp_proxy()


@app.command("serve-api")
def serve_api(
    host: str = typer.Option("127.0.0.1", help="Host to bind HTTP API"),
    port: int = typer.Option(8787, help="Port for HTTP API"),
):
    """Start the B-TWIN HTTP API server for orchestration workflow."""
    import uvicorn
    from btwin_cli.api_app import create_default_app

    console.print(f"[bold]Starting B-TWIN HTTP API on http://{host}:{port}[/bold]")
    from btwin_cli.doc_sync import sync_global_dirs, sync_global_docs
    active_data_dir = _get_active_data_dir()
    sync_global_docs(active_data_dir)
    sync_global_dirs(active_data_dir)
    app_instance = create_default_app()
    uvicorn.run(app_instance, host=host, port=port)


@app.command()
def search(query: str, n: int = typer.Option(5, help="Number of results")):
    """Search past entries by semantic similarity."""
    config = _get_config()
    if _use_attached_api(config):
        response = _attached_api_call_or_exit("/api/entries/search", {"query": query, "nResults": n, "scope": "all"})
        results = response.get("results", [])
    else:
        from btwin_core.btwin import BTwin

        twin = BTwin(config)
        results = twin.search(query, n_results=n)

    if not results:
        console.print("[yellow]No matching records found.[/yellow]")
        return

    for r in results:
        meta = r["metadata"]
        record_id = meta.get("record_id", meta.get("slug", "unknown"))
        console.print(f"\n[bold cyan]{meta.get('date', '')}/{record_id}[/bold cyan]")
        console.print(Markdown(r["content"][:500]))
        console.print("---")


@app.command()
def record(
    content: str,
    tldr: str = typer.Option(..., help="Required 1-3 sentence summary for search indexing"),
    topic: str = typer.Option(None, help="Topic slug"),
):
    """Manually record a note."""
    config = _get_config()
    if _use_attached_api(config):
        payload: dict[str, object] = {"content": content, "tldr": tldr}
        if topic:
            payload["topic"] = topic
        result = _attached_api_call_or_exit("/api/entries/record", payload)
    else:
        from btwin_core.btwin import BTwin

        twin = BTwin(config)
        result = twin.record(content, topic=topic, tldr=tldr)
    console.print(f"[green]Recorded: {result['path']}[/green]")


@handoff_app.callback()
def handoff(
    ctx: typer.Context,
    record_id: str | None = typer.Option(None, "--record-id", help="Btwin record id for the handoff"),
    summary: str | None = typer.Option(None, "--summary", help="One-line handoff summary"),
    dispatch: str | None = typer.Option(None, "--dispatch", help="Copy-paste dispatch sentence for the next worker"),
    branch: str | None = typer.Option(None, "--branch", help="Branch name for starter context"),
    commit: str | None = typer.Option(None, "--commit", help="Relevant commit SHA"),
    tag: list[str] = typer.Option([], "--tag", help="Optional archive tag (repeatable)"),
    background: str | None = typer.Option(None, "--background", help="Background section"),
    intent: str | None = typer.Option(None, "--intent", help="Intent and decisions section"),
    current_state: str | None = typer.Option(None, "--current-state", help="Current state section"),
    verification: str | None = typer.Option(None, "--verification", help="Verification section"),
    risks: str | None = typer.Option(None, "--risks", help="Risks and open questions section"),
    next_steps: str | None = typer.Option(None, "--next-steps", help="Next steps section"),
    starter_context: str | None = typer.Option(None, "--starter-context", help="Starter context section"),
):
    """Write the latest project-local handoff snapshot and append a global archive row."""
    if ctx.invoked_subcommand is not None:
        return

    missing = [
        option
        for option, value in (
            ("--record-id", record_id),
            ("--summary", summary),
            ("--dispatch", dispatch),
        )
        if not value
    ]
    if missing:
        console.print(
            "[red]Missing required handoff write options.[/red]\n"
            f"- Required: {', '.join(missing)}\n"
            "Use [bold]btwin handoff --help[/bold] for write usage or "
            "[bold]btwin handoff list[/bold] to inspect history."
        )
        raise typer.Exit(2)

    result = write_handoff_record(
        _project_root(),
        record_id=record_id,
        summary=summary,
        dispatch=dispatch,
        branch=branch,
        commit=commit,
        tags=tag,
        background=background,
        intent=intent,
        current_state=current_state,
        verification=verification,
        risks=risks,
        next_steps=next_steps,
        starter_context=starter_context,
    )
    console.print(f"[green]Updated local handoff snapshot[/green] -> {result.snapshot_path}")
    console.print(f"[dim]Global archive appended[/dim] -> {result.archive_path}")


@handoff_app.command("list")
def handoff_list(
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum number of recent handoffs to show"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output"),
):
    """List recent handoffs for the current project from the global archive."""
    payload = list_handoff_records(_project_root(), limit=limit)
    _emit_payload(payload, as_json=as_json)


@handoff_app.command("show")
def handoff_show(
    record_id: str | None = typer.Argument(None, help="Btwin record id to show; defaults to the latest handoff"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output"),
):
    """Show one archived handoff for the current project."""
    payload = get_handoff_record(_project_root(), record_id=record_id)
    if payload is None:
        target = record_id or "latest"
        console.print(f"[red]Handoff not found[/red]: {target}")
        raise typer.Exit(4)
    _emit_payload(payload, as_json=as_json)


@app.command()
def chat():
    """Interactive chat with B-TWIN (REPL mode). Requires API key."""
    from btwin_core.btwin import BTwin

    config = _get_config()
    if _use_attached_api(config):
        console.print(
            "[red]Chat mode is only supported in standalone runtime mode.[/red]\n"
            "Set `runtime.mode: standalone` in ~/.btwin/config.yaml for local chat,\n"
            "or use B-TWIN through MCP/serve-api for shared attached mode."
        )
        raise typer.Exit(1)
    if not config.llm.api_key:
        console.print(
            "[red]API key not configured.[/red]\n"
            "Chat mode requires a direct LLM API key.\n"
            "Run [bold]btwin setup[/bold] and enable API key, "
            "or use B-TWIN via MCP with Claude/Codex instead."
        )
        raise typer.Exit(1)
    twin = BTwin(config)

    console.print("[bold]B-TWIN Chat[/bold] — Type /quit to exit, /end to end session.\n")

    while True:
        try:
            user_input = console.input("[bold green]> [/bold green]")
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.strip() == "/quit":
            result = twin.end_session()
            if result:
                console.print(f"\n[dim]Session saved: {result['date']}/{result['slug']}[/dim]")
            break

        if user_input.strip() == "/end":
            result = twin.end_session()
            if result:
                console.print(f"\n[dim]Session saved: {result['date']}/{result['slug']}[/dim]")
            else:
                console.print("[yellow]No active session.[/yellow]")
            continue

        if not user_input.strip():
            continue

        response = twin.chat(user_input)
        console.print(f"\n[bold blue]B-TWIN:[/bold blue] {response}\n")


@sources_app.command("list")
def sources_list(refresh: bool = typer.Option(False, "--refresh", help="Refresh entry counts before listing")):
    """List registered data sources."""
    registry = _get_registry()
    registry.ensure_global_default()
    if refresh:
        sources = registry.refresh_entry_counts()
    else:
        sources = registry.load()

    if not sources:
        console.print("[yellow]No data sources registered.[/yellow]")
        return

    for s in sources:
        status = "enabled" if s.enabled else "disabled"
        console.print(
            f"- [bold]{s.name}[/bold] ({status})\n"
            f"  path: {s.path}\n"
            f"  entries: {s.entry_count}\n"
            f"  last_scanned_at: {s.last_scanned_at or '-'}"
        )


@sources_app.command("add")
def sources_add(
    path: str = typer.Argument(..., help="Path to .btwin directory or project root containing .btwin"),
    name: str | None = typer.Option(None, help="Optional source name"),
    disabled: bool = typer.Option(False, "--disabled", help="Register source as disabled"),
):
    """Add a data source."""
    registry = _get_registry()
    p = Path(path).expanduser()

    # Allow passing either the .btwin dir or a project root containing .btwin
    candidate = p / ".btwin" if p.name != ".btwin" and (p / ".btwin").is_dir() else p

    if not candidate.exists() or not candidate.is_dir():
        raise typer.BadParameter(f"Source directory not found: {candidate}")

    src = registry.add_source(candidate, name=name, enabled=not disabled)
    state = "enabled" if src.enabled else "disabled"
    console.print(f"[green]Added source:[/green] {src.name} ({state}) -> {src.path}")


@sources_app.command("scan")
def sources_scan(
    root: str = typer.Argument(..., help="Root directory to scan for .btwin folders"),
    max_depth: int = typer.Option(4, help="Maximum scan depth"),
    register: bool = typer.Option(False, "--register", help="Register all discovered sources"),
):
    """Scan for candidate .btwin directories under a root path."""
    registry = _get_registry()
    candidates = registry.scan_for_btwin_dirs([Path(root)], max_depth=max_depth)

    if not candidates:
        console.print("[yellow]No .btwin directories found.[/yellow]")
        return

    console.print(f"Found {len(candidates)} candidate(s):")
    for c in candidates:
        console.print(f"- {c}")

    if register:
        for c in candidates:
            registry.add_source(c)
        console.print("[green]Registered all discovered sources.[/green]")


@sources_app.command("refresh")
def sources_refresh():
    """Refresh entry counts and scan timestamps for registered sources."""
    registry = _get_registry()
    registry.ensure_global_default()
    updated = registry.refresh_entry_counts()
    console.print(f"[green]Refreshed {len(updated)} source(s).[/green]")


@promotion_app.command("schedule")
def promotion_schedule(
    set_value: str | None = typer.Option(None, "--set", help="Set cron-style schedule expression"),
):
    """Show or update promotion batch schedule."""
    config_path = _config_path()

    if set_value is None:
        config = _get_config()
        console.print(f"Promotion schedule: [bold]{config.promotion.schedule}[/bold]")
        console.print(f"Enabled: {'yes' if config.promotion.enabled else 'no'}")
        return

    if not _is_valid_cron_schedule(set_value):
        raise typer.BadParameter("Invalid cron format. Expected 5 fields, e.g. '0 9,21 * * *'")

    raw: object = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}

    data: dict[str, object] = raw if isinstance(raw, dict) else {}

    promotion_raw = data.get("promotion", {})
    promotion_cfg: dict[str, object]
    if isinstance(promotion_raw, dict):
        promotion_cfg = dict(promotion_raw)
    else:
        promotion_cfg = {}

    promotion_cfg["enabled"] = bool(promotion_cfg.get("enabled", True))
    promotion_cfg["schedule"] = set_value
    data["promotion"] = promotion_cfg

    _atomic_write_yaml(config_path, data)
    console.print(f"[green]Promotion schedule updated:[/green] {set_value}")


@promotion_app.command("run")
def promotion_run(limit: int | None = typer.Option(None, min=1, help="Max approved items to process")):
    """Run one promotion batch (approved -> queued -> promoted)."""
    from btwin_core.indexer import CoreIndexer
    from btwin_core.promotion_store import PromotionStore
    from btwin_core.promotion_worker import PromotionWorker
    from btwin_core.storage import Storage

    config = _get_config()
    storage = Storage(config.data_dir)
    store = PromotionStore(config.data_dir / "promotion_queue.yaml")
    indexer = CoreIndexer(data_dir=config.data_dir)
    worker = PromotionWorker(storage=storage, promotion_store=store, indexer=indexer)

    result = worker.run_once(limit=limit)
    console.print(
        "[green]Promotion batch done[/green] "
        f"processed={result['processed']} promoted={result['promoted']} "
        f"skipped={result['skipped']} errors={result['errors']}"
    )


@indexer_app.command("status")
def indexer_status():
    """Show indexer manifest status summary."""
    from btwin_core.indexer import CoreIndexer

    config = _get_config()
    idx = CoreIndexer(data_dir=config.data_dir)
    summary = idx.status_summary()
    console.print(
        "Indexer status "
        f"total={summary.get('total', 0)} "
        f"indexed={summary.get('indexed', 0)} "
        f"pending={summary.get('pending', 0)} "
        f"stale={summary.get('stale', 0)} "
        f"failed={summary.get('failed', 0)} "
        f"deleted={summary.get('deleted', 0)}"
    )


@indexer_app.command("refresh")
def indexer_refresh(limit: int | None = typer.Option(None, min=1, help="Max docs to process in this run")):
    """Refresh pending/stale/failed/deleted docs into vector index."""
    from btwin_core.indexer import CoreIndexer

    config = _get_config()
    idx = CoreIndexer(data_dir=config.data_dir)
    result = idx.refresh(limit=limit)
    console.print(
        "Indexer refresh "
        f"processed={result['processed']} indexed={result['indexed']} "
        f"deleted={result['deleted']} failed={result['failed']}"
    )


@indexer_app.command("reconcile")
def indexer_reconcile():
    """Reconcile file system docs with manifest and refresh index."""
    from btwin_core.indexer import CoreIndexer

    config = _get_config()
    idx = CoreIndexer(data_dir=config.data_dir)
    result = idx.reconcile()
    console.print(
        "Indexer reconcile "
        f"processed={result['processed']} indexed={result['indexed']} "
        f"deleted={result['deleted']} failed={result['failed']}"
    )


@indexer_app.command("repair")
def indexer_repair(doc_id: str = typer.Option(..., "--doc-id", help="Document id to repair")):
    """Repair a single manifest doc id by re-indexing source content."""
    from btwin_core.indexer import CoreIndexer

    config = _get_config()
    idx = CoreIndexer(data_dir=config.data_dir)
    result = idx.repair(doc_id)
    status = "ok" if result.get("ok") else "failed"
    console.print(f"Indexer repair {status} doc_id={doc_id} status={result.get('status')}")


@indexer_app.command("kpi")
def indexer_kpi():
    """Show sync-gap KPI metrics for indexer health."""
    from btwin_core.indexer import CoreIndexer

    config = _get_config()
    idx = CoreIndexer(data_dir=config.data_dir)
    kpi = idx.kpi_summary()
    console.print(
        "Indexer KPI "
        f"write_to_indexed_latency_ms_avg={kpi.get('write_to_indexed_latency_ms_avg')} "
        f"manifest_vector_mismatch_count={kpi.get('manifest_vector_mismatch_count')} "
        f"repair_success_rate={kpi.get('repair_success_rate')} "
        f"repair_avg_duration_ms={kpi.get('repair_avg_duration_ms')}"
    )


def _effective_runtime_openclaw_path(config: BTwinConfig) -> str | None:
    if config.runtime.mode == "standalone":
        return None

    env_path = os.environ.get("BTWIN_OPENCLAW_CONFIG_PATH")
    if env_path:
        return env_path

    if config.runtime.openclaw_config_path:
        return str(config.runtime.openclaw_config_path)

    return None


@runtime_app.command("show")
def runtime_show():
    """Show current runtime mode and OpenClaw config path."""
    config = _get_config()
    configured_openclaw_path = config.runtime.openclaw_config_path
    effective_openclaw_path = _effective_runtime_openclaw_path(config)
    console.print(f"Runtime mode: {config.runtime.mode}")
    console.print(f"Configured OpenClaw config path: {configured_openclaw_path if configured_openclaw_path else '-'}")
    console.print(f"Effective OpenClaw config path: {effective_openclaw_path if effective_openclaw_path else '-'}")
    if config.runtime.mode == "attached":
        console.print("Recall adapter target: openclaw")
        console.print("Attached fallback behavior: standalone-journal if OpenClaw memory binding is unavailable")
    else:
        console.print("Recall adapter target: standalone-journal")


@runtime_app.command("bind")
def runtime_bind(
    thread_id: str = typer.Option(..., "--thread", help="Thread id"),
    agent_name: str = typer.Option(..., "--agent", help="Agent name"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Bind the current project runtime context to a thread and agent."""
    config = _get_config()
    thread = _resolve_runtime_thread(thread_id, config)
    if thread is None:
        console.print(f"[red]Thread not found:[/red] {thread_id}")
        raise typer.Exit(4)

    agent_store = _get_agent_store()
    agent = agent_store.get_agent(agent_name)
    if agent is None:
        console.print(f"[red]Agent not found:[/red] {agent_name}")
        raise typer.Exit(4)

    participant_names = _thread_participant_names(thread)
    if not participant_names:
        console.print(f"[red]Thread participant data unavailable:[/red] {thread_id}")
        raise typer.Exit(4)
    if agent_name not in participant_names:
        console.print(
            f"[red]Agent is not a participant on this thread:[/red] {agent_name} not in {thread_id}"
        )
        raise typer.Exit(4)

    binding = _get_runtime_binding_store().bind(thread_id=thread_id, agent_name=agent_name)
    payload = _runtime_binding_payload(
        RuntimeBindingState(binding=binding),
        config=config,
        agent_store=agent_store,
        thread=thread,
    )
    _emit_payload(payload, as_json=as_json)


@runtime_app.command("current")
def runtime_current(
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show the current project runtime binding."""
    config = _get_config()
    state = _get_runtime_binding_store().read_state()
    payload = _runtime_binding_payload(state, config=config, include_thread_lookup_error=True)
    _emit_payload(payload, as_json=as_json)


@runtime_app.command("clear")
def runtime_clear(
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Clear the current project runtime binding."""
    config = _get_config()
    previous = _get_runtime_binding_store().clear()
    previous_thread = None
    if previous.binding is not None:
        try:
            previous_thread = _resolve_runtime_thread(previous.binding.thread_id, config)
        except Exception:
            previous_thread = None
    payload = {
        "cleared": True,
        "bound": False,
        "previous_binding": previous.binding.model_dump() if previous.binding is not None else None,
        "previous_thread": previous_thread,
        "previous_binding_error": previous.binding_error,
    }
    _emit_payload(payload, as_json=as_json)


_PLATFORMS = {
    "claude": ("Claude Code", (".claude/commands/",), ()),
    "codex": ("Codex", (), (".agents/skills/",)),
    "gemini": ("Gemini", (".gemini/commands/",), ()),
}

_INIT_PROVIDERS = set(available_provider_names())

_EXCLUDED_BUNDLED_SKILLS = {"bt-sync"}


def _get_skills_dir() -> Path:
    """Return the path to the skills directory bundled with btwin."""
    return resolve_bundled_skills_dir() or Path(__file__).resolve().parent / "skills"


def _validate_init_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in _INIT_PROVIDERS:
        console.print(
            f"[red]Unsupported provider:[/red] {provider}\n"
            f"Supported providers: {', '.join(sorted(_INIT_PROVIDERS))}"
        )
        raise typer.Exit(1)
    return normalized


def _providers_config_path(data_dir: Path | None = None) -> Path:
    return (data_dir or _get_active_data_dir()) / "providers.json"


def _collect_skill_dirs(skills_dir: Path) -> list[Path]:
    """Return sorted list of bt-* skill directories that contain SKILL.md."""
    if not skills_dir.is_dir():
        return []
    return sorted(
        d for d in skills_dir.iterdir()
        if d.is_dir()
        and d.name.startswith("bt-")
        and d.name not in _EXCLUDED_BUNDLED_SKILLS
        and (d / "SKILL.md").is_file()
    )


def _parse_skill_name(skill_md: Path) -> str | None:
    """Extract the 'name' field from SKILL.md YAML frontmatter."""
    try:
        lines = skill_md.read_text().splitlines()
        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                if line.startswith("name:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _install_skills_to(target_dir: Path, skill_dirs: list[Path]) -> int:
    """Create symlinks in target_dir for each skill. Returns count installed."""
    target_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for skill_dir in skill_dirs:
        source_path = skill_dir / "SKILL.md"
        skill_name = _parse_skill_name(source_path) or skill_dir.name
        link_path = target_dir / f"{skill_name}.md"
        if link_path.exists() or link_path.is_symlink():
            link_path.unlink()
        link_path.symlink_to(source_path)
        count += 1
    return count


def _install_skill_dirs_to(target_dir: Path, skill_dirs: list[Path]) -> int:
    """Create directory symlinks in target_dir for each skill. Returns count installed."""
    target_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for skill_dir in skill_dirs:
        link_path = target_dir / skill_dir.name
        if link_path.is_symlink() or link_path.is_file():
            link_path.unlink()
        elif link_path.is_dir():
            shutil.rmtree(link_path)
        link_path.symlink_to(skill_dir, target_is_directory=True)
        count += 1
    return count


@app.command("install-skills")
def install_skills(
    local: bool = typer.Option(False, "--local", help="Install to current project instead of global"),
    platform: str = typer.Option("", "--platform", help="Platform name (claude/codex/gemini) for non-interactive mode"),
):
    """Install B-TWIN orchestration skills to client-specific skill locations."""
    skills_dir = _get_skills_dir()
    skill_dirs = _collect_skill_dirs(skills_dir)

    if not skill_dirs:
        console.print("[red]No skills found to install.[/red]")
        raise typer.Exit(1)

    # Determine selected platforms
    if platform:
        keys = [k.strip().lower() for k in platform.split(",")]
        for k in keys:
            if k not in _PLATFORMS:
                console.print(f"[red]Unknown platform: {k}[/red]")
                console.print(f"Available: {', '.join(_PLATFORMS.keys())}")
                raise typer.Exit(1)
        selected = keys
    else:
        # Interactive selection
        console.print("[bold]Available platforms:[/bold]")
        items = list(_PLATFORMS.items())
        for i, (key, (label, _, _)) in enumerate(items, 1):
            console.print(f"  {i}. {label}")
        raw = console.input("\nSelect platforms (comma-separated, e.g. 1,2): ")
        selected = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                idx = int(token) - 1
                if 0 <= idx < len(items):
                    selected.append(items[idx][0])
                else:
                    console.print(f"[red]Invalid selection: {token}[/red]")
                    raise typer.Exit(1)
            except ValueError:
                console.print(f"[red]Invalid selection: {token}[/red]")
                raise typer.Exit(1)

    if not selected:
        console.print("[yellow]No platforms selected.[/yellow]")
        raise typer.Exit(1)

    # Install to each selected platform
    for key in selected:
        label, rel_paths, skill_dir_paths = _PLATFORMS[key]
        for rel_path in rel_paths:
            if local:
                target = Path.cwd() / rel_path
            else:
                target = Path.home() / rel_path
            count = _install_skills_to(target, skill_dirs)
            console.print(f"[green]{count} skills installed to {target}[/green]")
        for rel_path in skill_dir_paths:
            if local:
                target = Path.cwd() / rel_path
            else:
                target = Path.home() / rel_path
            count = _install_skill_dirs_to(target, skill_dirs)
            console.print(f"[green]{count} skills installed to {target}[/green]")


@app.command("migrate-collab")
def migrate_collab_cmd(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Preview without changes"),
    data_dir: str = typer.Option(None, "--data-dir", help="Override data directory"),
):
    """Migrate legacy collab records to workflow format."""
    from btwin_cli.migration import migrate_collab_to_workflow

    if data_dir:
        path = Path(data_dir)
    else:
        config = _get_config()
        path = config.data_dir

    result = migrate_collab_to_workflow(path, dry_run=dry_run)

    if dry_run:
        console.print("[bold]Dry run results:[/bold]")
        console.print(f"  Would migrate: {result['would_migrate']}")
        console.print(f"  Already migrated (skipped): {result['skipped']}")
        console.print(f"  Errors: {result['errors']}")
        if result["would_migrate"] > 0:
            console.print("\n[dim]Run with --no-dry-run to apply changes.[/dim]")
    else:
        console.print("[bold]Migration results:[/bold]")
        console.print(f"  [green]Migrated: {result['migrated']}[/green]")
        console.print(f"  Skipped: {result['skipped']}")
        console.print(f"  [red]Errors: {result['errors']}[/red]")


@app.command()
def validate(
    fix: bool = typer.Option(False, "--fix", help="Auto-fix fixable issues"),
    reconcile: bool = typer.Option(False, "--reconcile", help="Run indexer reconcile after fix"),
    data_dir: str = typer.Option(None, "--data-dir", help="Override data directory"),
):
    """Validate all entries against the canonical frontmatter schema."""
    from btwin_core.validator import validate_entry, fix_entry

    config = _get_config()
    entries_dir = Path(data_dir) / "entries" if data_dir else config.data_dir / "entries"

    if not entries_dir.exists():
        console.print(f"[red]Entries directory not found: {entries_dir}[/red]")
        raise typer.Exit(1)

    files = sorted(entries_dir.rglob("*.md"))
    total = len(files)
    invalid = 0
    fixed = 0

    for f in files:
        result = validate_entry(f)
        if not result.valid:
            invalid += 1
            rel = f.relative_to(entries_dir)
            console.print(f"[yellow]{rel}[/yellow]")
            for issue in result.issues:
                console.print(f"  - {issue}")
            if fix:
                if fix_entry(f):
                    fixed += 1
                    console.print(f"  [green]fixed[/green]")

    console.print(f"\nTotal: {total}, Invalid: {invalid}, Fixed: {fixed}")

    if reconcile and fixed > 0:
        from btwin_core.indexer import CoreIndexer
        idx = CoreIndexer(data_dir=config.data_dir)
        result = idx.reconcile()
        console.print(
            f"Reconcile: processed={result['processed']} indexed={result['indexed']} "
            f"deleted={result['deleted']} failed={result['failed']}"
        )


if __name__ == "__main__":
    app()
