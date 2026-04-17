import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolSection, ProtocolStore
from btwin_core.runtime_binding_store import RuntimeBindingStore
import btwin_core.runtime_binding_store as runtime_binding_store
from btwin_core.thread_store import ThreadStore
from btwin_core.workflow_event_log import WorkflowEventLog


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def _parse_json_output(output: str):
    return json.loads(output.strip())


def _seed_context(tmp_path: Path):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol = Protocol(
        name="workflow-hook",
        description="Hook command protocol",
        phases=[
            ProtocolPhase(
                name="implementation",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
            )
        ],
    )
    protocol_store.save_protocol(protocol)
    thread = thread_store.create_thread(
        topic="Workflow hook",
        protocol="workflow-hook",
        participants=["alice"],
        initial_phase="implementation",
    )
    return project_root, data_dir, thread_store, thread


def test_workflow_hook_stop_returns_block_when_contribution_is_missing(tmp_path, monkeypatch):
    project_root, data_dir, _thread_store, thread = _seed_context(tmp_path)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "workflow",
            "hook",
            "--event",
            "Stop",
            "--thread",
            thread["thread_id"],
            "--agent",
            "alice",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = _parse_json_output(result.output)
    assert payload["event"] == "Stop"
    assert payload["decision"] == "block"
    assert payload["reason"] == "missing_contribution"
    assert payload["required_result_recorded"] is False


def test_workflow_hook_user_prompt_submit_returns_overlay(tmp_path, monkeypatch):
    project_root, data_dir, _thread_store, thread = _seed_context(tmp_path)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "workflow",
            "hook",
            "--event",
            "UserPromptSubmit",
            "--thread",
            thread["thread_id"],
            "--agent",
            "alice",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["event"] == "UserPromptSubmit"
    assert payload["decision"] == "noop"
    assert "Required result type: contribution." in payload["overlay"]


def test_workflow_hook_stop_allows_after_contribution_submit(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_context(tmp_path)
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "implementation",
        content="## completed\nDone.\n",
        tldr="implemented",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "workflow",
            "hook",
            "--event",
            "Stop",
            "--thread",
            thread["thread_id"],
            "--agent",
            "alice",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["event"] == "Stop"
    assert payload["decision"] == "allow"
    assert payload["required_result_recorded"] is True


def test_workflow_hook_reads_stdin_user_prompt_submit_and_emits_empty_success(tmp_path, monkeypatch):
    project_root, data_dir, _thread_store, thread = _seed_context(tmp_path)
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    payload = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": str(project_root),
        "hook_event_name": "UserPromptSubmit",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
        "prompt": "continue",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""

    events = WorkflowEventLog(_thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    assert [event["event_type"] for event in events] == ["phase_attempt_started"]
    assert events[0]["hook_event_name"] == "UserPromptSubmit"


def test_workflow_hook_reads_stdin_session_start_refreshes_binding_metadata(tmp_path, monkeypatch):
    project_root, data_dir, _thread_store, thread = _seed_context(tmp_path)
    timestamps = iter([
        "2026-04-15T00:00:00+00:00",
        "2026-04-15T00:05:00+00:00",
    ])
    monkeypatch.setattr(runtime_binding_store, "_now_iso", lambda: next(timestamps))
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    payload = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": str(project_root),
        "hook_event_name": "SessionStart",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    output = _parse_json_output(result.output)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Resume thread" in output["hookSpecificOutput"]["additionalContext"]

    binding_payload = json.loads((project_root / ".btwin" / "runtime" / "binding.json").read_text(encoding="utf-8"))
    assert binding_payload["thread_id"] == thread["thread_id"]
    assert binding_payload["agent_name"] == "alice"
    assert binding_payload["status"] == "active"
    assert binding_payload["bound_at"] == "2026-04-15T00:00:00+00:00"
    assert binding_payload["opened_at"] == "2026-04-15T00:00:00+00:00"
    assert binding_payload["last_seen_at"] == "2026-04-15T00:05:00+00:00"
    assert binding_payload["closed_at"] is None
    assert binding_payload["closed_reason"] is None


def test_workflow_hook_reads_stdin_user_prompt_submit_refreshes_binding_metadata(tmp_path, monkeypatch):
    project_root, data_dir, _thread_store, thread = _seed_context(tmp_path)
    timestamps = iter([
        "2026-04-15T00:00:00+00:00",
        "2026-04-15T00:05:00+00:00",
    ])
    monkeypatch.setattr(runtime_binding_store, "_now_iso", lambda: next(timestamps))
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    payload = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": str(project_root),
        "hook_event_name": "UserPromptSubmit",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
        "prompt": "continue",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""

    binding_payload = json.loads((project_root / ".btwin" / "runtime" / "binding.json").read_text(encoding="utf-8"))
    assert binding_payload["last_seen_at"] == "2026-04-15T00:05:00+00:00"
    assert binding_payload["status"] == "active"


def test_workflow_hook_reads_stdin_stop_refreshes_binding_metadata(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_context(tmp_path)
    timestamps = iter([
        "2026-04-15T00:00:00+00:00",
        "2026-04-15T00:05:00+00:00",
    ])
    monkeypatch.setattr(runtime_binding_store, "_now_iso", lambda: next(timestamps))
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "implementation",
        content="## completed\nDone.\n",
        tldr="implemented",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    payload = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": str(project_root),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""

    binding_payload = json.loads((project_root / ".btwin" / "runtime" / "binding.json").read_text(encoding="utf-8"))
    assert binding_payload["last_seen_at"] == "2026-04-15T00:05:00+00:00"
    assert binding_payload["status"] == "active"


def test_workflow_hook_reads_stdin_session_start_ignores_binding_refresh_write_failure(tmp_path, monkeypatch):
    project_root, data_dir, _thread_store, thread = _seed_context(tmp_path)
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    real_store = RuntimeBindingStore(project_root / ".btwin")

    class FailingRefreshStore:
        def read_state(self):
            return real_store.read_state()

        def observe_session_start(self, binding):
            raise OSError("binding file locked")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_runtime_binding_store", lambda: FailingRefreshStore())

    payload = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": str(project_root),
        "hook_event_name": "SessionStart",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    output = _parse_json_output(result.output)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Resume thread" in output["hookSpecificOutput"]["additionalContext"]


def test_workflow_hook_reads_stdin_stop_and_emits_block_reason(tmp_path, monkeypatch):
    project_root, data_dir, _thread_store, thread = _seed_context(tmp_path)
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    payload = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": str(project_root),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    output = _parse_json_output(result.output)
    assert output["decision"] == "block"
    assert "still needs a contribution" in output["reason"]

    events = WorkflowEventLog(_thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    assert [event["event_type"] for event in events] == [
        "phase_exit_check_requested",
        "phase_exit_blocked",
    ]
    assert events[0]["hook_event_name"] == "Stop"
    assert events[1]["hook_event_name"] == "Stop"


def test_workflow_hook_reads_stdin_stop_and_emits_empty_success_when_allowed(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_context(tmp_path)
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "implementation",
        content="## completed\nDone.\n",
        tldr="implemented",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    payload = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": str(project_root),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""

    events = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    assert [event["event_type"] for event in events] == ["phase_exit_check_requested"]
    assert events[0]["hook_event_name"] == "Stop"


def test_workflow_hook_stdin_mode_fails_open_without_runtime_binding(tmp_path, monkeypatch):
    project_root, data_dir, _thread_store, _thread = _seed_context(tmp_path)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    payload = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": str(project_root),
        "hook_event_name": "UserPromptSubmit",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
        "prompt": "continue",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_workflow_hook_stdin_mode_records_block_event(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_context(tmp_path)
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    payload = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": str(project_root),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
        "turn_id": "turn-1",
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }
    result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output

    events = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    assert [event["event_type"] for event in events] == [
        "phase_exit_check_requested",
        "phase_exit_blocked",
    ]
    assert events[-1]["decision"] == "block"
    assert events[-1]["hook_event_name"] == "Stop"
    assert events[-1]["agent"] == "alice"
    assert events[-1]["scope"] == "local_recovery"
    assert events[-1]["cycle_finished"] is False


def test_contribution_submit_records_workflow_event(tmp_path, monkeypatch):
    project_root, data_dir, thread_store, thread = _seed_context(tmp_path)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(
        app,
        [
            "contribution",
            "submit",
            "--thread",
            thread["thread_id"],
            "--agent",
            "alice",
            "--phase",
            "implementation",
            "--content",
            "## completed\nDone.\n",
            "--tldr",
            "implemented",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output

    events = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "required_result_recorded"
    assert events[0]["agent"] == "alice"
    assert events[0]["phase"] == "implementation"
    assert events[0]["summary"] == "implemented"


def test_contribution_submit_uses_attached_api_when_runtime_is_attached(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    project_root.mkdir()
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "contribution_id": "contrib-1",
            "agent": data["agentName"],
            "phase": data["phase"],
            "tldr": data["tldr"],
        }

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    result = runner.invoke(
        app,
        [
            "contribution",
            "submit",
            "--thread",
            "thread-1",
            "--agent",
            "alice",
            "--phase",
            "implementation",
            "--content",
            "## completed\nDone.\n",
            "--tldr",
            "implemented",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "/api/threads/thread-1/contributions",
            {
                "agentName": "alice",
                "phase": "implementation",
                "content": "## completed\nDone.\n",
                "tldr": "implemented",
            },
        )
    ]
    payload = _parse_json_output(result.output)
    assert payload["contribution_id"] == "contrib-1"
