import json
from pathlib import Path

import httpx
import yaml
from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig


runner = CliRunner()


def _attached_config() -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"))


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _parse_json_output(output: str):
    return json.loads(output.strip())


def test_thread_create_attached_uses_shared_api(monkeypatch):
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "thread_id": "thread-20260413-abc123",
            "topic": data["topic"],
            "protocol": data["protocol"],
            "participants": [{"name": name, "joined_at": "2026-04-13T00:00:00+00:00"} for name in data["participants"]],
            "status": "active",
            "current_phase": "context",
        }

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    result = runner.invoke(
        app,
        [
            "thread",
            "create",
            "--topic",
            "Orchestration",
            "--protocol",
            "debate",
            "--participant",
            "alice",
            "--participant",
            "bob",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "/api/threads",
            {"topic": "Orchestration", "protocol": "debate", "participants": ["alice", "bob"]},
        )
    ]
    payload = _parse_json_output(result.output)
    assert payload["thread_id"] == "thread-20260413-abc123"
    assert payload["current_phase"] == "context"


def test_thread_list_attached_uses_shared_api(monkeypatch):
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    def fake_attached_get(path: str, params: dict) -> list[dict]:
        calls.append((path, params))
        return [
            {"thread_id": "thread-1", "status": params.get("status", "active"), "topic": "Alpha"},
            {"thread_id": "thread-2", "status": "completed", "topic": "Beta"},
        ]

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)

    result = runner.invoke(app, ["thread", "list", "--status", "active", "--json"])

    assert result.exit_code == 0, result.output
    assert calls == [("/api/threads", {"status": "active"})]
    payload = _parse_json_output(result.output)
    assert [item["thread_id"] for item in payload] == ["thread-1", "thread-2"]


def test_thread_close_attached_uses_shared_api(monkeypatch):
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "thread_id": data.get("thread_id", "thread-1"),
            "status": "completed",
            "summary": data["summary"],
            "decision": data["decision"],
            "result_record_id": "record-123",
        }

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    result = runner.invoke(
        app,
        [
            "thread",
            "close",
            "--thread",
            "thread-1",
            "--summary",
            "Ship it",
            "--decision",
            "merge to main",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "/api/threads/thread-1/close",
            {"summary": "Ship it", "decision": "merge to main"},
        )
    ]
    payload = _parse_json_output(result.output)
    assert payload["status"] == "completed"
    assert payload["result_record_id"] == "record-123"


def test_thread_show_attached_uses_shared_api(monkeypatch):
    calls: list[tuple[str, dict | None]] = []

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    def fake_attached_get(path: str, params: dict | None = None):
        calls.append((path, params))
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached thread",
                "protocol": "debate",
                "status": "active",
                "current_phase": "context",
            }
        if path == "/api/threads/thread-1/status":
            return {
                "thread_id": "thread-1",
                "status": "active",
                "current_phase": "context",
                "unread_message_count": 2,
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)

    result = runner.invoke(app, ["thread", "show", "thread-1", "--json"])

    assert result.exit_code == 0, result.output
    assert calls == [
        ("/api/threads/thread-1", None),
        ("/api/threads/thread-1/status", None),
    ]
    payload = _parse_json_output(result.output)
    assert payload["thread_id"] == "thread-1"
    assert payload["status_summary"]["unread_message_count"] == 2


def test_thread_show_attached_404_preserves_not_found_exit_code(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    request = httpx.Request("GET", "http://127.0.0.1:8788/api/threads/thread-1")
    response = httpx.Response(404, request=request, json={"detail": "Thread 'thread-1' not found"})

    def fail_api_get(path: str, params: dict | None = None):
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    monkeypatch.setattr(main, "_api_get", fail_api_get)

    result = runner.invoke(app, ["thread", "show", "thread-1", "--json"])

    assert result.exit_code == 4
    assert "Thread 'thread-1' not found" in result.output


def test_thread_send_message_attached_uses_shared_api(monkeypatch):
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "message_id": "msg-1",
            "thread_id": "thread-1",
            "from_agent": data["fromAgent"],
            "content": data["content"],
            "tldr": data["tldr"],
            "delivery_mode": data["deliveryMode"],
            "target_agents": data["targetAgents"],
        }

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    result = runner.invoke(
        app,
        [
            "thread",
            "send-message",
            "--thread",
            "thread-1",
            "--from",
            "alice",
            "--content",
            "Please review this.",
            "--tldr",
            "review request",
            "--delivery-mode",
            "direct",
            "--target",
            "bob",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "/api/threads/thread-1/messages",
            {
                "fromAgent": "alice",
                "content": "Please review this.",
                "tldr": "review request",
                "deliveryMode": "direct",
                "targetAgents": ["bob"],
            },
        )
    ]
    payload = _parse_json_output(result.output)
    assert payload["message_id"] == "msg-1"
    assert payload["target_agents"] == ["bob"]


def test_thread_send_message_attached_404_preserves_not_found_exit_code(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    request = httpx.Request("POST", "http://127.0.0.1:8788/api/threads/thread-1/messages")
    response = httpx.Response(404, request=request, json={"detail": "Thread 'thread-1' not found"})

    def fail_api_post(path: str, data: dict) -> dict:
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    monkeypatch.setattr(main, "_api_post", fail_api_post)

    result = runner.invoke(
        app,
        [
            "thread",
            "send-message",
            "--thread",
            "thread-1",
            "--from",
            "alice",
            "--content",
            "Please review this.",
            "--tldr",
            "review request",
            "--json",
        ],
    )

    assert result.exit_code == 4
    assert "Thread 'thread-1' not found" in result.output


def test_thread_inbox_attached_uses_shared_api(monkeypatch):
    calls: list[tuple[str, dict | None]] = []

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    def fake_attached_get(path: str, params: dict | None = None):
        calls.append((path, params))
        return {
            "thread_id": "thread-1",
            "agent": "alice",
            "pending_count": 1,
            "messages": [
                {
                    "message_id": "msg-1",
                    "tldr": "review request",
                }
            ],
        }

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)

    result = runner.invoke(
        app,
        ["thread", "inbox", "--thread", "thread-1", "--agent", "alice", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("/api/threads/thread-1/inbox", {"agent": "alice"}),
    ]
    payload = _parse_json_output(result.output)
    assert payload["pending_count"] == 1
    assert payload["messages"][0]["message_id"] == "msg-1"


def test_thread_status_attached_uses_shared_api(monkeypatch):
    calls: list[tuple[str, dict | None]] = []

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    def fake_attached_get(path: str, params: dict | None = None):
        calls.append((path, params))
        return {
            "thread_id": "thread-1",
            "agent": "alice",
            "participant_status": "joined",
            "pending_count": 1,
            "current_phase": "context",
        }

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)

    result = runner.invoke(
        app,
        ["thread", "status", "--thread", "thread-1", "--agent", "alice", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("/api/threads/thread-1/status", {"agent": "alice"}),
    ]
    payload = _parse_json_output(result.output)
    assert payload["participant_status"] == "joined"
    assert payload["pending_count"] == 1


def test_thread_list_attached_reports_http_status_errors_without_unreachable_message(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())

    request = httpx.Request("GET", "http://127.0.0.1:8788/api/threads")
    response = httpx.Response(
        409,
        request=request,
        json={"detail": "shared API responded but the request was rejected"},
    )

    def fail_attached_get(path: str, params: dict | None = None):
        raise httpx.HTTPStatusError("conflict", request=request, response=response)

    monkeypatch.setattr(main, "_api_get", fail_attached_get)

    result = runner.invoke(app, ["thread", "list", "--json"])

    assert result.exit_code == 1
    assert "shared API responded with an error" in result.output
    assert "409" in result.output
    assert "shared API responded but the request was rejected" in result.output
    assert "could not reach the shared B-TWIN API" not in result.output


def test_thread_list_attached_reports_path_mismatch_hint_on_connect_error(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())
    monkeypatch.setattr(main, "_config_path", lambda: tmp_path / "attached-config.yaml")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda config=None: tmp_path / "attached-data")
    monkeypatch.setattr(main, "_api_base_url", lambda: "http://127.0.0.1:8788")
    monkeypatch.setattr(main, "_current_btwin_command_path", lambda: Path("/tmp/current-btwin"))
    monkeypatch.setattr(main.shutil, "which", lambda name: "/tmp/path-btwin" if name == "btwin" else None)

    request = httpx.Request("GET", "http://127.0.0.1:8788/api/threads")

    def fail_attached_get(path: str, params: dict | None = None):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(main, "_api_get", fail_attached_get)

    result = runner.invoke(app, ["thread", "list", "--json"])

    assert result.exit_code == 1
    assert "could not reach the shared B-TWIN API" in result.output
    assert "Possible PATH mismatch" in result.output
    assert "/tmp/current-btwin" in result.output
    assert "/tmp/path-btwin" in result.output
    assert "BTWIN_CONFIG_PATH" in result.output or "attached-config.yaml" in result.output


def test_thread_list_attached_reports_stale_proxy_hint_when_paths_match(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())
    monkeypatch.setattr(main, "_config_path", lambda: tmp_path / "attached-config.yaml")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda config=None: tmp_path / "attached-data")
    monkeypatch.setattr(main, "_api_base_url", lambda: "http://127.0.0.1:8788")
    monkeypatch.setattr(main, "_current_btwin_command_path", lambda: Path("/private/tmp/current-btwin"))
    monkeypatch.setattr(main.shutil, "which", lambda name: "/private/tmp/current-btwin" if name == "btwin" else None)

    request = httpx.Request("GET", "http://127.0.0.1:8788/api/threads")

    def fail_attached_get(path: str, params: dict | None = None):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(main, "_api_get", fail_attached_get)

    result = runner.invoke(app, ["thread", "list", "--json"])

    assert result.exit_code == 1
    assert "stale MCP proxy or stale Codex client session" in result.output
    assert "restart your MCP client session" in result.output


def test_thread_lifecycle_standalone_creates_lists_and_closes_with_result_entry(tmp_path, monkeypatch):
    data_dir = tmp_path / ".btwin"
    monkeypatch.setattr(main, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    create_result = runner.invoke(
        app,
        [
            "thread",
            "create",
            "--topic",
            "Stand-alone orchestration",
            "--protocol",
            "debate",
            "--participant",
            "alice",
            "--json",
        ],
    )

    assert create_result.exit_code == 0, create_result.output
    created = _parse_json_output(create_result.output)
    thread_id = created["thread_id"]

    list_active_result = runner.invoke(
        app,
        ["thread", "list", "--status", "active", "--json"],
    )

    assert list_active_result.exit_code == 0, list_active_result.output
    active_payload = _parse_json_output(list_active_result.output)
    assert [item["thread_id"] for item in active_payload] == [thread_id]

    close_result = runner.invoke(
        app,
        [
            "thread",
            "close",
            "--thread",
            thread_id,
            "--summary",
            "Stand-alone summary",
            "--decision",
            "Promote the plan",
            "--json",
        ],
    )

    assert close_result.exit_code == 0, close_result.output
    closed_payload = _parse_json_output(close_result.output)
    assert closed_payload["status"] == "completed"
    result_record_id = closed_payload["result_record_id"]

    list_completed_result = runner.invoke(
        app,
        ["thread", "list", "--status", "completed", "--json"],
    )

    assert list_completed_result.exit_code == 0, list_completed_result.output
    completed_payload = _parse_json_output(list_completed_result.output)
    assert [item["thread_id"] for item in completed_payload] == [thread_id]

    entry_file = next((data_dir / "entries" / "entry").rglob(f"{result_record_id}.md"), None)
    assert entry_file is not None, "expected standalone close to create a btwin entry"

    raw = entry_file.read_text(encoding="utf-8")
    parts = raw.split("---\n", 2)
    assert len(parts) >= 3
    metadata = yaml.safe_load(parts[1]) or {}
    assert "thread-result" in metadata["tags"]
    assert f"thread:{thread_id}" in metadata["related_records"]
    assert "## Thread" in parts[2]


def test_thread_close_standalone_omits_result_id_when_link_update_fails(tmp_path, monkeypatch):
    data_dir = tmp_path / ".btwin"
    monkeypatch.setattr(main, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    import btwin_core.btwin as btwin_module

    monkeypatch.setattr(
        btwin_module.BTwin,
        "update_entry",
        lambda self, **kwargs: {"ok": False, "error": "record_not_found", "record_id": kwargs["record_id"]},
    )

    create_result = runner.invoke(
        app,
        [
            "thread",
            "create",
            "--topic",
            "Fallback check",
            "--protocol",
            "debate",
            "--participant",
            "alice",
            "--json",
        ],
    )

    assert create_result.exit_code == 0, create_result.output
    thread_id = _parse_json_output(create_result.output)["thread_id"]

    close_result = runner.invoke(
        app,
        [
            "thread",
            "close",
            "--thread",
            thread_id,
            "--summary",
            "Fallback summary",
            "--json",
        ],
    )

    assert close_result.exit_code == 0, close_result.output
    closed_payload = _parse_json_output(close_result.output)
    assert closed_payload["status"] == "completed"
    assert "result_record_id" not in closed_payload
