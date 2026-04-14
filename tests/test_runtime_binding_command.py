import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.runtime_binding_store import RuntimeBindingStore
from btwin_core.thread_store import ThreadStore


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def _parse_json_output(output: str):
    return json.loads(output.strip())


def _seed_runtime_context(tmp_path: Path):
    project_root = tmp_path / "project"
    agent_data_dir = tmp_path / "global-btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    agent_store = AgentStore(agent_data_dir)
    agent_store.register(
        name="alice",
        model="gpt-5",
        alias="alice",
        provider="codex",
        role="implementer",
    )
    thread = thread_store.create_thread(
        topic="Runtime binding",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    return project_root, agent_store, thread_store, thread


def test_runtime_bind_persists_binding_and_current(tmp_path, monkeypatch):
    project_root, agent_store, thread_store, thread = _seed_runtime_context(tmp_path)
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    bind_result = runner.invoke(
        app,
        [
            "runtime",
            "bind",
            "--thread",
            thread["thread_id"],
            "--agent",
            "alice",
            "--json",
        ],
    )

    assert bind_result.exit_code == 0, bind_result.output
    bind_payload = _parse_json_output(bind_result.output)
    assert bind_payload["bound"] is True
    assert bind_payload["binding"]["thread_id"] == thread["thread_id"]
    assert bind_payload["binding"]["agent_name"] == "alice"
    assert bind_payload["binding"]["bound_at"]

    binding_file = project_root / ".btwin" / "runtime" / "binding.json"
    assert binding_file.exists()

    current_result = runner.invoke(app, ["runtime", "current", "--json"])
    assert current_result.exit_code == 0, current_result.output
    current_payload = _parse_json_output(current_result.output)
    assert current_payload["bound"] is True
    assert current_payload["binding"] == bind_payload["binding"]
    assert current_payload["binding_error"] is None


def test_runtime_bind_attached_resolves_thread_via_shared_api(tmp_path, monkeypatch):
    project_root, agent_store, thread_store, thread = _seed_runtime_context(tmp_path)
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)

    def fail_local_thread_store():
        raise AssertionError("local thread store should not be used in attached mode")

    monkeypatch.setattr(main, "_get_thread_store", fail_local_thread_store)

    attached_calls: list[str] = []

    def fake_attached_get(path: str, params: dict | None = None):
        assert params is None
        attached_calls.append(path)
        if path == f"/api/threads/{thread['thread_id']}":
            return thread
        raise AssertionError(path)

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)
    monkeypatch.setattr(main, "_api_get", fake_attached_get)

    bind_result = runner.invoke(
        app,
        [
            "runtime",
            "bind",
            "--thread",
            thread["thread_id"],
            "--agent",
            "alice",
            "--json",
        ],
    )

    assert bind_result.exit_code == 0, bind_result.output
    bind_payload = _parse_json_output(bind_result.output)
    assert bind_payload["bound"] is True
    assert bind_payload["thread"]["thread_id"] == thread["thread_id"]
    assert bind_payload["thread"]["topic"] == thread["topic"]

    current_result = runner.invoke(app, ["runtime", "current", "--json"])
    assert current_result.exit_code == 0, current_result.output
    current_payload = _parse_json_output(current_result.output)
    assert current_payload["bound"] is True
    assert current_payload["thread"]["thread_id"] == thread["thread_id"]
    assert current_payload["thread"]["topic"] == thread["topic"]

    assert attached_calls == [f"/api/threads/{thread['thread_id']}", f"/api/threads/{thread['thread_id']}"]


def test_runtime_bind_rejects_missing_thread_or_agent(tmp_path, monkeypatch):
    project_root, agent_store, thread_store, _thread = _seed_runtime_context(tmp_path)
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    missing_thread = runner.invoke(
        app,
        [
            "runtime",
            "bind",
            "--thread",
            "thread-missing",
            "--agent",
            "alice",
        ],
    )
    assert missing_thread.exit_code == 4
    assert "Thread not found" in missing_thread.output

    missing_agent = runner.invoke(
        app,
        [
            "runtime",
            "bind",
            "--thread",
            thread_store.list_threads()[0]["thread_id"],
            "--agent",
            "bob",
        ],
    )
    assert missing_agent.exit_code == 4
    assert "Agent not found" in missing_agent.output

    assert not (project_root / ".btwin" / "runtime" / "binding.json").exists()


def test_runtime_bind_rejects_non_participant_agent_standalone(tmp_path, monkeypatch):
    project_root, agent_store, thread_store, thread = _seed_runtime_context(tmp_path)
    data_dir = tmp_path / ".btwin"
    agent_store.register(
        name="bob",
        model="gpt-5",
        alias="bob",
        provider="codex",
        role="implementer",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(
        app,
        [
            "runtime",
            "bind",
            "--thread",
            thread["thread_id"],
            "--agent",
            "bob",
        ],
    )

    assert result.exit_code == 4
    assert "not a participant" in result.output
    assert not (project_root / ".btwin" / "runtime" / "binding.json").exists()


def test_runtime_bind_rejects_non_participant_agent_attached(tmp_path, monkeypatch):
    project_root, agent_store, thread_store, thread = _seed_runtime_context(tmp_path)
    data_dir = tmp_path / ".btwin"
    agent_store.register(
        name="bob",
        model="gpt-5",
        alias="bob",
        provider="codex",
        role="implementer",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    def fake_attached_get(path: str, params: dict | None = None):
        assert params is None
        if path == f"/api/threads/{thread['thread_id']}":
            thread_payload = dict(thread)
            thread_payload["participants"] = [{"name": "alice", "joined_at": thread["participants"][0]["joined_at"]}]
            return thread_payload
        raise AssertionError(path)

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)

    result = runner.invoke(
        app,
        [
            "runtime",
            "bind",
            "--thread",
            thread["thread_id"],
            "--agent",
            "bob",
        ],
    )

    assert result.exit_code == 4
    assert "not a participant" in result.output
    assert not (project_root / ".btwin" / "runtime" / "binding.json").exists()


def test_runtime_clear_removes_binding(tmp_path, monkeypatch):
    project_root, agent_store, thread_store, thread = _seed_runtime_context(tmp_path)
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    bind_result = runner.invoke(
        app,
        [
            "runtime",
            "bind",
            "--thread",
            thread["thread_id"],
            "--agent",
            "alice",
            "--json",
        ],
    )
    assert bind_result.exit_code == 0, bind_result.output

    clear_result = runner.invoke(app, ["runtime", "clear", "--json"])
    assert clear_result.exit_code == 0, clear_result.output
    clear_payload = _parse_json_output(clear_result.output)
    assert clear_payload["cleared"] is True
    assert clear_payload["previous_binding"]["thread_id"] == thread["thread_id"]

    current_result = runner.invoke(app, ["runtime", "current", "--json"])
    assert current_result.exit_code == 0, current_result.output
    current_payload = _parse_json_output(current_result.output)
    assert current_payload["bound"] is False
    assert current_payload["binding"] is None
    assert current_payload["binding_error"] is None


def test_runtime_current_reports_malformed_binding_error(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    binding_file = project_root / ".btwin" / "runtime" / "binding.json"
    binding_file.parent.mkdir(parents=True, exist_ok=True)
    binding_file.write_text("{not-json", encoding="utf-8")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    current_result = runner.invoke(app, ["runtime", "current", "--json"])
    assert current_result.exit_code == 0, current_result.output
    current_payload = _parse_json_output(current_result.output)
    assert current_payload["bound"] is False
    assert current_payload["binding"] is None
    assert current_payload["binding_error"]
    assert "Failed to load runtime binding" in current_payload["binding_error"]


def test_runtime_current_is_best_effort_in_attached_mode(tmp_path, monkeypatch):
    project_root, agent_store, thread_store, thread = _seed_runtime_context(tmp_path)
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    binding_store = RuntimeBindingStore(project_root / ".btwin")
    binding_store.bind(thread["thread_id"], "alice")

    def fail_api_get(path: str, params: dict | None = None):
        raise RuntimeError("shared api unavailable")

    monkeypatch.setattr(main, "_api_get", fail_api_get)

    current_result = runner.invoke(app, ["runtime", "current", "--json"])
    assert current_result.exit_code == 0, current_result.output
    current_payload = _parse_json_output(current_result.output)
    assert current_payload["bound"] is True
    assert current_payload["binding"]["thread_id"] == thread["thread_id"]
    assert current_payload["thread_error"]
    assert "shared api unavailable" in current_payload["thread_error"]


def test_runtime_clear_reports_malformed_binding_error(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    binding_file = project_root / ".btwin" / "runtime" / "binding.json"
    binding_file.parent.mkdir(parents=True, exist_ok=True)
    binding_file.write_text("{not-json", encoding="utf-8")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    clear_result = runner.invoke(app, ["runtime", "clear", "--json"])
    assert clear_result.exit_code == 0, clear_result.output
    clear_payload = _parse_json_output(clear_result.output)
    assert clear_payload["cleared"] is True
    assert clear_payload["bound"] is False
    assert clear_payload["previous_binding"] is None
    assert clear_payload["previous_binding_error"]
    assert "Failed to load runtime binding" in clear_payload["previous_binding_error"]
    assert not binding_file.exists()
