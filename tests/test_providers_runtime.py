from pathlib import Path

from btwin_cli.api_terminals import _load_providers
from btwin_core.agent_runner import AgentRunner
from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig
from btwin_core.event_bus import EventBus
from btwin_core.protocol_store import ProtocolStore
from btwin_core.providers import ClaudeCodeProvider, CodexProvider
from btwin_core.thread_store import ThreadStore


def test_terminal_provider_loader_returns_unconfigured_payload_when_missing(tmp_path):
    payload = _load_providers(tmp_path)

    assert payload["configured"] is False
    assert payload["providers"] == []
    assert "btwin init --provider codex" in payload["setup_hint"]


def test_agent_runner_provider_loader_returns_empty_without_user_config(tmp_path):
    assert AgentRunner._load_providers(tmp_path / "providers.json") == []


def test_claude_provider_normalizes_recognized_stream_events() -> None:
    provider = ClaudeCodeProvider()

    session_started = provider.parse_stream_line(
        '{"type":"system","session_id":"claude-session","message":{"content":[{"type":"text","text":"boot"}]}}'
    )
    text_delta = provider.parse_stream_line(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello "},{"type":"text","text":"world"}]}}'
    )
    turn_complete = provider.parse_stream_line(
        '{"type":"result","session_id":"claude-session","result":"done"}'
    )

    assert session_started is not None
    assert session_started.event_type == "session_started"
    assert session_started.session_id == "claude-session"
    assert session_started.raw["type"] == "system"

    assert text_delta is not None
    assert text_delta.event_type == "text_delta"
    assert text_delta.text_delta == "Hello world"
    assert text_delta.raw["type"] == "assistant"

    assert turn_complete is not None
    assert turn_complete.event_type == "turn_complete"
    assert turn_complete.is_final is True
    assert turn_complete.final_text == "done"
    assert turn_complete.session_id == "claude-session"
    assert turn_complete.raw["type"] == "result"


def test_codex_provider_normalizes_recognized_stream_events_and_preserves_unknown() -> None:
    provider = CodexProvider()

    session_started = provider.parse_stream_line(
        '{"type":"thread.started","thread_id":"thread-123","metadata":{"source":"codex"}}'
    )
    turn_complete = provider.parse_stream_line(
        '{"type":"item.completed","thread_id":"thread-123","item":{"type":"agent_message","text":"Hello from Codex"}}'
    )
    unknown = provider.parse_stream_line('{"type":"hook","name":"pre_tool"}')

    assert session_started is not None
    assert session_started.event_type == "session_started"
    assert session_started.session_id == "thread-123"
    assert session_started.raw["type"] == "thread.started"

    assert turn_complete is not None
    assert turn_complete.event_type == "turn_complete"
    assert turn_complete.text_delta == "Hello from Codex"
    assert turn_complete.is_final is True
    assert turn_complete.final_text == "Hello from Codex"
    assert turn_complete.session_id == "thread-123"
    assert turn_complete.raw["type"] == "item.completed"

    assert unknown is not None
    assert unknown.event_type == "hook"
    assert unknown.raw["type"] == "hook"


def test_codex_provider_build_command_enables_hooks_for_new_sessions() -> None:
    provider = CodexProvider()

    cmd = provider.build_command(session_id=None, bypass_permissions=False)

    assert cmd[:3] == ["codex", "exec", "--enable"]
    assert "codex_hooks" in cmd
    assert "--json" in cmd
    assert "--skip-git-repo-check" in cmd


def test_codex_provider_build_command_enables_hooks_for_resume_sessions() -> None:
    provider = CodexProvider()

    cmd = provider.build_command(session_id="thread-123", bypass_permissions=True)

    assert cmd[:5] == ["codex", "exec", "resume", "thread-123", "--enable"]
    assert "codex_hooks" in cmd
    assert "--json" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd


def test_agent_runner_prefers_explicit_agent_provider_over_model_lookup(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    providers_path = data_dir / "providers.json"
    providers_path.write_text('{"providers":[]}', encoding="utf-8")

    agent_store = AgentStore(data_dir)
    agent_store.register(
        "alice",
        model="gpt-5",
        provider="codex",
        role="implementer",
    )

    runner = AgentRunner(
        thread_store=ThreadStore(data_dir / "threads"),
        protocol_store=ProtocolStore(data_dir / "protocols"),
        agent_store=agent_store,
        event_bus=EventBus(),
        providers_path=providers_path,
        config=BTwinConfig(data_dir=data_dir),
    )

    agent = agent_store.get_agent("alice")

    assert agent is not None
    assert runner._resolve_provider(agent) == "codex"
