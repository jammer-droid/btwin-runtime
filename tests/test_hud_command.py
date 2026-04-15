import io
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.runtime_binding_store import RuntimeBindingStore
from btwin_core.thread_store import ThreadStore
from btwin_core.workflow_event_log import WorkflowEventLog


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def test_hud_without_binding_shows_runtime_summary(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "B-TWIN HUD" in result.output
    assert "binding=none" in result.output


def test_hud_with_binding_shows_bound_thread_and_recent_events(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    agent_store = AgentStore(data_dir)
    agent_store.register(
        name="alice",
        model="gpt-5",
        alias="alice",
        provider="codex",
        role="implementer",
    )
    thread = thread_store.create_thread(
        topic="HUD thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).append(
        {
            "timestamp": "2026-04-15T02:12:16+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "phase": "context",
            "event_type": "hook_decision",
            "hook_event_name": "Stop",
            "decision": "allow",
            "summary": "Stop allowed.",
        }
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "binding=alice" in result.output
    assert thread["thread_id"] in result.output
    assert "phase=context" in result.output
    assert "Stop allowed." in result.output


def test_follow_render_loop_uses_live_updates_without_clearing(monkeypatch):
    updates: list[str] = []

    class FakeLive:
        def __init__(self, initial, console=None, auto_refresh=False, screen=False):
            updates.append(initial)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, renderable, refresh=False):
            updates.append(renderable)

    def fail_clear():
        raise AssertionError("console.clear should not be called during follow mode")

    sleep_calls = {"count": 0}

    def fake_sleep(_interval: float):
        sleep_calls["count"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "Live", FakeLive)
    monkeypatch.setattr(main.console, "clear", fail_clear)
    monkeypatch.setattr(main.time, "sleep", fake_sleep)

    main._run_live_view(lambda: "HUD frame", interval=0.2)

    assert updates == ["HUD frame", "HUD frame"]


def test_hud_stream_prints_existing_events_for_bound_thread(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    thread = thread_store.create_thread(
        topic="HUD stream thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).append(
        {
            "timestamp": "2026-04-15T02:12:16+00:00",
            "thread_id": thread["thread_id"],
            "agent": "alice",
            "phase": "context",
            "event_type": "hook_decision",
            "hook_event_name": "Stop",
            "decision": "allow",
            "summary": "Stop allowed.",
        }
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    def fake_sleep(_interval: float):
        raise KeyboardInterrupt

    monkeypatch.setattr(main.time, "sleep", fake_sleep)

    result = runner.invoke(app, ["hud", "--stream"])

    assert result.exit_code == 0, result.output
    assert "B-TWIN HUD stream" in result.output
    assert thread["thread_id"] in result.output
    assert "Stop allow" in result.output
    assert "Stop allowed." in result.output


def test_render_thread_watch_formats_codex_and_btwin_events_for_humans():
    thread = {
        "thread_id": "thread-1",
        "protocol": "debate",
        "current_phase": "context",
        "topic": "HUD thread",
    }
    status_summary = {
        "agents": [
            {"name": "alice", "status": "contributed"},
        ]
    }
    events = [
        {
            "timestamp": "2026-04-15T04:04:50+00:00",
            "thread_id": "thread-1",
            "event_type": "hook_received",
            "source": "codex.hook",
            "agent": "alice",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "hook_event_name": "Stop",
            "summary": "Stop received.",
        },
        {
            "timestamp": "2026-04-15T04:04:50+00:00",
            "thread_id": "thread-1",
            "event_type": "hook_decision",
            "source": "btwin.workflow.hook",
            "agent": "alice",
            "phase": "context",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "hook_event_name": "Stop",
            "decision": "block",
            "reason": "missing_contribution",
            "summary": "Current phase context still needs a contribution from alice before stopping.",
        },
    ]

    rendered = main._render_thread_watch(thread, status_summary, events)

    assert "04:04:50  CODEX -> BTWIN  Stop check requested" in rendered
    assert "04:04:50  BTWIN -> CODEX  Stop blocked" in rendered
    assert "agent: alice" in rendered
    assert "phase: context" in rendered
    assert "reason: missing_contribution" in rendered
    assert "session: session-1" in rendered
    assert "turn: turn-1" in rendered
    assert "summary: Stop received." in rendered


def test_hud_attached_mode_shows_thread_lookup_error_instead_of_exiting(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    RuntimeBindingStore(project_root / ".btwin").bind("thread-missing", "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        raise RuntimeError(f"404 thread missing for {path}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert "binding=alice" in result.output
    assert "thread lookup error" in result.output
    assert "thread-missing" in result.output


def test_hud_key_parser_understands_arrow_and_control_keys():
    assert main._hud_key_from_bytes(b"\x1b[A") == "up"
    assert main._hud_key_from_bytes(b"\x1b[B") == "down"
    assert main._hud_key_from_bytes(b"\r") == "enter"
    assert main._hud_key_from_bytes(b"b") == "back"
    assert main._hud_key_from_bytes(b"c") == "close"
    assert main._hud_key_from_bytes(b"q") == "quit"


def test_hud_menu_escapes_literal_brackets_for_rich():
    rendered = main._render_hud_menu(main._HudNavigatorState())
    buffer = io.StringIO()
    Console(file=buffer, force_terminal=False, color_system=None).print(rendered)

    assert "> [threads]" in buffer.getvalue()


def test_hud_navigator_moves_from_menu_to_threads_to_thread_view(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads":
            return [
                {
                    "thread_id": "thread-1",
                    "topic": "First thread",
                    "protocol": "debate",
                    "current_phase": "context",
                    "status": "active",
                },
                {
                    "thread_id": "thread-2",
                    "topic": "Second thread",
                    "protocol": "debate",
                    "current_phase": "discussion",
                    "status": "active",
                },
            ]
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    assert main._apply_hud_key(state, "enter", config) is False
    assert state.screen == "threads"

    assert main._apply_hud_key(state, "down", config) is False
    assert state.thread_index == 1

    assert main._apply_hud_key(state, "enter", config) is False
    assert state.screen == "thread"
    assert state.selected_thread_id == "thread-2"

    assert main._apply_hud_key(state, "back", config) is False
    assert state.screen == "threads"


def test_hud_close_key_closes_selected_standalone_thread_and_hides_it(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    project_root.mkdir()
    thread = thread_store.create_thread(
        topic="Closable HUD thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    config = _standalone_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id=thread["thread_id"])

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    assert main._apply_hud_key(state, "close", config) is False
    assert state.screen == "threads"
    assert state.selected_thread_id is None

    rendered = main._render_hud_threads(state, config, limit=5)
    assert "No active threads" in rendered

    closed = thread_store.get_thread(thread["thread_id"])
    assert closed is not None
    assert closed["status"] == "completed"


def test_hud_close_key_uses_attached_close_api(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    config = _attached_config(data_dir)
    state = main._HudNavigatorState(screen="thread", selected_thread_id="thread-2")
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(main, "_project_root", lambda: project_root)

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {"thread_id": "thread-2", "status": "completed"}

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    assert main._apply_hud_key(state, "close", config) is False
    assert state.screen == "threads"
    assert state.selected_thread_id is None
    assert calls == [
        (
            "/api/threads/thread-2/close",
            {"summary": "Closed from B-TWIN HUD."},
        )
    ]


def test_hud_default_uses_navigator_when_interactive(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    called = {"value": False}

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_hud_is_interactive", lambda: True)
    monkeypatch.setattr(main, "_run_hud_navigator", lambda limit, interval: called.__setitem__("value", True))

    result = runner.invoke(app, ["hud"])

    assert result.exit_code == 0, result.output
    assert called["value"] is True


def test_hud_threads_picker_allows_selecting_attached_thread(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads":
            return [
                {
                    "thread_id": "thread-1",
                    "topic": "First thread",
                    "protocol": "debate",
                    "current_phase": "context",
                    "status": "active",
                }
            ]
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "First thread",
                "protocol": "debate",
                "current_phase": "context",
            }
        if path == "/api/threads/thread-1/status":
            return {
                "thread_id": "thread-1",
                "current_phase": "context",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)

    result = runner.invoke(app, ["hud", "--threads"], input="1\n1\n")

    assert result.exit_code == 0, result.output
    assert "Views" in result.output
    assert "threads" in result.output
    assert "Active Threads" in result.output
    assert "thread-1" in result.output
    assert "First thread" in result.output
    assert "phase=context" in result.output


def test_hud_threads_picker_can_transition_into_stream(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_api_get(path: str, params: dict | None = None):
        if path == "/api/threads":
            return [
                {
                    "thread_id": "thread-2",
                    "topic": "Stream thread",
                    "protocol": "debate",
                    "current_phase": "context",
                    "status": "active",
                }
            ]
        if path == "/api/threads/thread-2":
            return {
                "thread_id": "thread-2",
                "topic": "Stream thread",
                "protocol": "debate",
                "current_phase": "context",
            }
        if path == "/api/threads/thread-2/status":
            return {
                "thread_id": "thread-2",
                "current_phase": "context",
                "agents": [{"name": "alice", "status": "joined"}],
            }
        raise AssertionError(f"unexpected path: {path} params={params}")

    monkeypatch.setattr(main, "_api_get", fake_api_get)
    monkeypatch.setattr(main, "_resolve_bound_thread_id", lambda: None)

    def fake_sleep(_interval: float):
        raise KeyboardInterrupt

    monkeypatch.setattr(main.time, "sleep", fake_sleep)

    result = runner.invoke(app, ["hud", "--threads", "--stream"], input="1\n1\n")

    assert result.exit_code == 0, result.output
    assert "B-TWIN HUD stream" in result.output
    assert "thread=thread-2" in result.output
