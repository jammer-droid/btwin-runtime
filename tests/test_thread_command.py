import json
from pathlib import Path

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
