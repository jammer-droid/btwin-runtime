import json
import queue
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig


runner = CliRunner()


def _attached_config() -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"))


def _parse_json_output(output: str):
    return json.loads(output.strip())


class _FakeConsole:
    def __init__(self):
        self.calls: list[dict[str, object]] = []
        self.stream_writes: list[str] = []
        self.file = self

    def write(self, value: str):
        self.stream_writes.append(value)

    def flush(self):
        return None

    def print(self, value="", *args, **kwargs):
        self.calls.append(
            {
                "value": value,
                "args": args,
                "kwargs": kwargs,
            }
        )


def test_live_threads_attached_renders_human_summary(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    def fake_attached_get(path: str, params=None):
        if path == "/api/threads":
            return [
                {
                    "thread_id": "thread-1",
                    "topic": "Live debate",
                    "protocol": "debate",
                    "status": "active",
                    "current_phase": "discussion",
                    "participants": [{"name": "user"}, {"name": "alice"}],
                }
            ]
        if path == "/api/agent-runtime-status":
            return {
                "agents": {
                    "alice": [{"thread_id": "thread-1", "status": "received"}],
                }
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)
    monkeypatch.setattr(main, "_api_get", fake_attached_get)

    result = runner.invoke(app, ["live", "threads"])

    assert result.exit_code == 0, result.output
    assert "Live debate" in result.output
    assert "attached_agents: alice" in result.output


def test_live_attach_attached_calls_spawn_agent(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())
    monkeypatch.setattr(main, "_project_root", lambda: Path("/tmp/test-project"))

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {"thread_id": "thread-1", "participants": [{"name": "user"}, {"name": "alice"}]}

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    result = runner.invoke(app, ["live", "attach", "--thread", "thread-1", "--agent", "alice", "--json"])

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "/api/threads/thread-1/spawn-agent",
            {
                "agentName": "alice",
                "bypassPermissions": True,
                "projectRoot": "/tmp/test-project",
            },
        )
    ]
    payload = _parse_json_output(result.output)
    assert payload["thread_id"] == "thread-1"


def test_live_recover_attached_calls_recover_agent(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())
    monkeypatch.setattr(main, "_project_root", lambda: Path("/tmp/test-project"))

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {"thread_id": "thread-1", "agent_name": "alice", "recoverable": True}

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    result = runner.invoke(app, ["live", "recover", "--thread", "thread-1", "--agent", "alice", "--json"])

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "/api/threads/thread-1/recover-agent",
            {
                "agentName": "alice",
                "bypassPermissions": True,
                "projectRoot": "/tmp/test-project",
            },
        )
    ]
    payload = _parse_json_output(result.output)
    assert payload["thread_id"] == "thread-1"


def test_live_close_attached_calls_thread_close(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {"thread_id": "thread-1", "status": "completed", "summary": data["summary"]}

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    result = runner.invoke(
        app,
        ["live", "close", "--thread", "thread-1", "--summary", "done", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [("/api/threads/thread-1/close", {"summary": "done"})]
    payload = _parse_json_output(result.output)
    assert payload["status"] == "completed"


def test_live_enter_renders_human_chat_and_event_reply(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())
    monkeypatch.setattr(
        main,
        "_load_live_enter_snapshot",
        lambda thread_id, actor, config=None: {
            "thread_id": thread_id,
            "topic": "Live debate",
            "protocol": "debate",
            "current_phase": "discussion",
            "participants": ["user", "alice"],
            "actor": actor,
            "interaction_mode": "orchestrated_chat",
            "pending_count": 0,
            "recent_messages": [
                {"from": "alice", "_content": "previous point", "target_agents": []},
            ],
            "attached_agents": ["alice"],
        },
    )

    event_queue: queue.Queue[dict] = queue.Queue()
    event_queue.put(
        {
            "type": "agent_session_state",
            "resource_id": "thread-1",
            "agent_name": "alice",
            "state": "thinking",
        }
    )
    event_queue.put(
        {
            "type": "message_sent",
            "resource_id": "thread-1",
            "from_agent": "alice",
            "content": "hello from alice",
            "message_id": "msg-2",
        }
    )
    monkeypatch.setattr(main, "_start_live_event_listener", lambda thread_id: event_queue)
    monkeypatch.setattr(main, "_render_live_inbox_messages", lambda thread_id, actor, *, seen_message_ids: 0)

    sent_messages: list[dict] = []

    def fake_send(thread_id, actor, decision, config=None):
        sent_messages.append(
            {
                "thread_id": thread_id,
                "from": actor,
                "content": decision.content,
                "delivery_mode": decision.mode,
                "target_agents": decision.targets,
            }
        )
        return {"message_id": "msg-1", "delivery_mode": decision.mode, "target_agents": decision.targets}

    monkeypatch.setattr(main, "_live_enter_send_message", fake_send)

    result = runner.invoke(
        app,
        ["live", "enter", "--thread", "thread-1", "--as", "user"],
        input="@alice hi\n/exit\n",
    )

    assert result.exit_code == 0, result.output
    assert "Live debate" in result.output
    assert "attached_agents: alice" in result.output
    assert "recent:" in result.output
    assert "alice: previous point" in result.output
    assert "you -> @alice: hi" in result.output
    assert "alice is thinking" in result.output
    assert "alice: hello from alice" in result.output
    assert sent_messages[0]["delivery_mode"] == "direct"


def test_render_live_event_uses_transient_status_line_for_interactive_updates(monkeypatch):
    fake_console = _FakeConsole()
    monkeypatch.setattr(main, "console", fake_console)
    status_display = main._LiveStatusDisplay(console=fake_console, enabled=True)
    seen_message_ids: set[str] = set()

    rendered_state = main._render_live_event(
        {
            "type": "agent_session_state",
            "resource_id": "thread-1",
            "agent_name": "alice",
            "state": "thinking",
        },
        actor="user",
        seen_message_ids=seen_message_ids,
        status_display=status_display,
    )
    rendered_message = main._render_live_event(
        {
            "type": "message_sent",
            "resource_id": "thread-1",
            "from_agent": "alice",
            "content": "hello from alice",
            "message_id": "msg-2",
        },
        actor="user",
        seen_message_ids=seen_message_ids,
        status_display=status_display,
    )

    assert rendered_state == 1
    assert rendered_message == 1
    assert fake_console.stream_writes == [
        "\r\x1b[2Kalice is thinking...",
        "\r\x1b[2K",
    ]
    assert fake_console.calls[0]["value"] == "alice: hello from alice"
