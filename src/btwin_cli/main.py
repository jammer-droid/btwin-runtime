"""B-TWIN CLI — packaged command-line implementation."""

from __future__ import annotations

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
import re
import shutil
import subprocess

import typer
import yaml
from rich.console import Console
from rich.markdown import Markdown

from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig, load_config
from btwin_core.handoff_archive import write_handoff_record
from btwin_core.protocol_store import Protocol, ProtocolStore
from btwin_core.protocol_validator import ProtocolValidator
from btwin_core.sources import SourceRegistry
from btwin_core.thread_store import ThreadStore
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
agent_app = typer.Typer(help="Manage B-TWIN agent definitions.")
protocol_app = typer.Typer(help="Manage B-TWIN protocol definitions.")
thread_app = typer.Typer(help="Manage B-TWIN protocol threads.")
contribution_app = typer.Typer(help="Manage B-TWIN protocol contributions.")
app.add_typer(sources_app, name="sources")
app.add_typer(promotion_app, name="promotion")
app.add_typer(indexer_app, name="indexer")
app.add_typer(runtime_app, name="runtime")
app.add_typer(agent_app, name="agent")
app.add_typer(protocol_app, name="protocol")
app.add_typer(thread_app, name="thread")
app.add_typer(contribution_app, name="contribution")

console = Console(soft_wrap=True)


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


def _api_base_url() -> str:
    return os.environ.get("BTWIN_API_URL", "http://localhost:8787")


def _api_post(path: str, data: dict) -> dict:
    import httpx

    with httpx.Client(base_url=_api_base_url(), timeout=30.0) as client:
        resp = client.post(path, json=data)
        resp.raise_for_status()
        return resp.json()


def _use_attached_api(config: BTwinConfig) -> bool:
    return config.runtime.mode == "attached"


def _attached_api_call_or_exit(path: str, data: dict) -> dict:
    import httpx

    try:
        return _api_post(path, data)
    except httpx.HTTPError as exc:
        console.print(
            "[red]Attached runtime could not reach the shared B-TWIN API.[/red]\n"
            f"- URL: {_api_base_url()}\n"
            "- Start or restart the API with: [bold]btwin serve-api[/bold]\n"
            "- If you use a custom endpoint, check [bold]BTWIN_API_URL[/bold]\n"
            "- For local-only usage, switch to [bold]runtime.mode: standalone[/bold] in ~/.btwin/config.yaml\n"
            f"- Error: {exc.__class__.__name__}: {exc}"
        )
        raise typer.Exit(1)

def _config_path() -> Path:
    return Path.home() / ".btwin" / "config.yaml"


def _btwin_data_dir() -> Path:
    return Path.home() / ".btwin"


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


def _resolve_thread_protocol_context(thread_id: str) -> tuple[dict, Protocol, object, list[str], list[dict]]:
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
    return thread, protocol, phase, phase_participants, contributions


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
    thread, protocol, phase, phase_participants, contributions = _resolve_thread_protocol_context(thread_id)
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
    thread, protocol, phase, phase_participants, contributions = _resolve_thread_protocol_context(thread_id)
    current_phase = thread.get("current_phase")

    validation = ProtocolValidator.validate_phase(
        phase_participants=phase_participants,
        template_sections=phase.template or [],
        contributions=contributions,
    )
    phase_index = next(
        (idx for idx, item in enumerate(protocol.phases) if item.name == current_phase),
        -1,
    )
    sequential_next = (
        protocol.phases[phase_index + 1].name
        if 0 <= phase_index < len(protocol.phases) - 1
        else None
    )

    branch_transitions = [t for t in protocol.transitions if t.from_phase == current_phase and t.on]
    default_transition = next(
        (t for t in protocol.transitions if t.from_phase == current_phase and t.on is None),
        None,
    )
    valid_outcomes = list(protocol.outcomes) or [
        transition.on for transition in branch_transitions if transition.on
    ]

    next_phase = None
    suggested_action = "close_thread"
    if not validation.passed:
        suggested_action = "submit_contribution"
    elif outcome:
        matched = next(
            (t for t in branch_transitions if t.on == outcome),
            None,
        )
        next_phase = matched.to if matched else None
        suggested_action = "advance_phase" if next_phase else "record_outcome"
    elif valid_outcomes:
        suggested_action = "record_outcome"
    else:
        next_phase = default_transition.to if default_transition else sequential_next
        if next_phase:
            suggested_action = "advance_phase"

    payload = {
        "thread_id": thread_id,
        "protocol": protocol.name,
        "current_phase": current_phase,
        "passed": validation.passed,
        "missing": validation.missing,
        "valid_outcomes": valid_outcomes,
        "requested_outcome": outcome,
        "next_phase": next_phase,
        "suggested_action": suggested_action,
    }
    _emit_payload(payload, as_json=as_json)


@thread_app.command("show")
def thread_show(
    thread_id: str = typer.Argument(..., help="Thread id"),
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show one thread and its current status summary."""
    store = _get_thread_store()
    thread = store.get_thread(thread_id)
    if thread is None:
        console.print(f"[red]Thread not found:[/red] {thread_id}")
        raise typer.Exit(4)

    payload = dict(thread)
    payload["status_summary"] = store.get_status(thread_id)
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

    message = _get_thread_store().send_message(
        thread_id=thread_id,
        from_agent=from_agent,
        content=_resolve_content(content),
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
    """Register B-TWIN MCP proxy for one provider.

    Today only Codex is supported. The provider flag remains explicit so future
    provider-specific initializers can plug into the same surface.
    """
    provider_name = _validate_init_provider(provider)

    if local:
        if project_name is None:
            project_name = _detect_project_name()

        codex_config_path = Path.cwd() / ".codex" / "config.toml"
        existing_paths = [path for path in (codex_config_path,) if path.exists()]

        if existing_paths and not force:
            existing_list = "\n".join(f"- {path.name if path.parent == Path.cwd() else path.relative_to(Path.cwd())}" for path in existing_paths)
            console.print(
                f"[yellow]Local {provider_name} config already exists[/yellow]\n"
                f"{existing_list}\n"
                "Use [bold]--force[/bold] to overwrite."
            )
            raise typer.Exit(1)

        _write_codex_project_config(codex_config_path, project_name)
        console.print(f"[green]Created local {provider_name} config[/green] (project: {project_name})")
        console.print("[dim]- Codex: .codex/config.toml[/dim]")
    else:
        console.print(f"[bold]Registering B-TWIN MCP for {provider_name}...[/bold]\n")
        _register_codex_global(force=force)

    console.print(
        "\n[bold]Ensure B-TWIN API server is running:[/bold]\n"
        "  launchctl kickstart -k gui/$(id -u)/com.btwin.serve-api\n"
        "  or: btwin serve-api\n"
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
        "  1. [bold]btwin serve-api[/bold]                  — Start the HTTP API on http://127.0.0.1:8787\n"
        "  2. [bold]btwin init[/bold]                       — Register btwin mcp-proxy for Codex / Claude\n"
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


@app.command()
def handoff(
    record_id: str = typer.Option(..., "--record-id", help="Btwin record id for the handoff"),
    summary: str = typer.Option(..., "--summary", help="One-line handoff summary"),
    dispatch: str = typer.Option(..., "--dispatch", help="Copy-paste dispatch sentence for the next worker"),
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


_PLATFORMS = {
    "claude": ("Claude Code", (".claude/commands/",), ()),
    "codex": ("Codex", (), (".agents/skills/",)),
    "gemini": ("Gemini", (".gemini/commands/",), ()),
}

_INIT_PROVIDERS = {"codex"}

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
