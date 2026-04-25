import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.delegation_state import DelegationState
from btwin_core.delegation_store import DelegationStore
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_store import PhaseCycleStore
from btwin_core.protocol_store import ProtocolStore, compile_protocol_definition
from btwin_core.thread_store import ThreadStore


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def _parse_json_output(output: str):
    return json.loads(output.strip())


def _seed_delegate_thread(data_dir: Path):
    thread_store = ThreadStore(data_dir / "threads")
    protocol_store = ProtocolStore(data_dir / "protocols")
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-review",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute", "review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    }
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Delegate thread",
        protocol="delegate-review",
        participants=["alice"],
        initial_phase="review",
    )
    return thread_store, thread


def _seed_waiting_delegate_thread(data_dir: Path):
    thread_store = ThreadStore(data_dir / "threads")
    protocol_store = ProtocolStore(data_dir / "protocols")
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-wait",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute", "review"],
                        "template": [{"section": "completed", "required": True}],
                        "outcome_policy": "review-outcomes",
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    },
                    {
                        "name": "followup",
                        "actions": ["contribute", "review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "implementer", "action": "revise", "alias": "Revise"},
                        ],
                    },
                ],
                "outcome_policies": [
                    {
                        "name": "review-outcomes",
                        "emitters": ["reviewer", "user"],
                        "actions": ["decide"],
                        "outcomes": ["retry", "accept"],
                    }
                ],
                "transitions": [
                    {"from": "review", "on": "retry", "to": "review", "alias": "Retry"},
                    {"from": "review", "on": "accept", "to": "followup", "alias": "Accept"},
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Delegate wait thread",
        protocol="delegate-wait",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(data_dir).write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "review",
        content="## Completed\n\nInitial review finished.",
        tldr="review done",
    )
    DelegationStore(data_dir).write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="waiting_for_human",
            updated_at="2026-04-20T00:00:00Z",
            loop_iteration=1,
            current_phase="review",
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent="alice",
            required_action="record_outcome",
            expected_output="record outcome: retry, accept",
            stop_reason="human_outcome_required",
        )
    )
    return thread_store, thread


def test_delegate_start_outputs_running_state(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / "custom-runtime-data"
    thread_store, thread = _seed_delegate_thread(data_dir)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    start_result = runner.invoke(
        app,
        ["delegate", "start", "--thread", thread["thread_id"], "--json"],
    )

    assert start_result.exit_code == 0, start_result.output
    start_payload = _parse_json_output(start_result.output)
    assert start_payload["status"] == "running"
    assert start_payload["target_role"] == "reviewer"
    assert start_payload["resolved_agent"] == "alice"
    assert start_payload["required_action"] == "submit_contribution"
    assert start_payload["expected_output"] == "review contribution"
    assert "reason_blocked" not in start_payload

    inbox = thread_store.list_inbox(thread["thread_id"], "alice")
    assert len(inbox) == 1
    assert (data_dir / "runtime" / "delegation-state.jsonl").exists()
    assert not (project_root / ".btwin" / "runtime" / "delegation-state.jsonl").exists()

    status_result = runner.invoke(
        app,
        ["delegate", "status", "--thread", thread["thread_id"], "--json"],
    )

    assert status_result.exit_code == 0, status_result.output
    status_payload = _parse_json_output(status_result.output)
    assert status_payload == start_payload


def test_delegate_commands_use_attached_api_when_attached(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: (_ for _ in ()).throw(AssertionError("local thread store should not be used")))
    monkeypatch.setattr(main, "_get_protocol_store", lambda: (_ for _ in ()).throw(AssertionError("local protocol store should not be used")))

    calls: list[tuple[str, object]] = []

    def fake_api_post(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "thread_id": "thread-1",
            "status": "running",
            "updated_at": "2026-04-20T00:00:00Z",
            "target_role": "reviewer",
            "resolved_agent": "alice",
            "required_action": "submit_contribution",
            "expected_output": "review contribution",
        }

    def fake_attached_get(path: str, params: dict | None = None) -> dict:
        calls.append((path, params))
        return {
            "thread_id": "thread-1",
            "status": "running",
            "updated_at": "2026-04-20T00:00:00Z",
            "target_role": "reviewer",
            "resolved_agent": "alice",
            "required_action": "submit_contribution",
            "expected_output": "review contribution",
        }

    monkeypatch.setattr(main, "_api_post", fake_api_post)
    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)

    start_result = runner.invoke(app, ["delegate", "start", "--thread", "thread-1", "--json"])
    assert start_result.exit_code == 0, start_result.output
    assert _parse_json_output(start_result.output)["status"] == "running"

    status_result = runner.invoke(app, ["delegate", "status", "--thread", "thread-1", "--json"])
    assert status_result.exit_code == 0, status_result.output
    assert _parse_json_output(status_result.output)["resolved_agent"] == "alice"

    assert calls == [
        ("/api/threads/thread-1/delegate/start", {}),
        ("/api/threads/thread-1/delegate/status", None),
    ]


def test_delegate_start_attached_json_preserves_blocked_payload(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    request = httpx.Request("POST", "http://test/api/threads/thread-1/delegate/start")
    response = httpx.Response(
        409,
        request=request,
        json={
            "detail": {
                "status": "blocked",
                "reason_blocked": "dispatch_failed",
                "target_role": "reviewer",
                "resolved_agent": "alice",
            }
        },
    )

    def fail_attached_call(path: str, data: dict) -> dict:
        raise AssertionError(f"unexpected attached helper call: {path} {data}")

    def fake_api_post(path: str, data: dict) -> dict:
        raise httpx.HTTPStatusError("conflict", request=request, response=response)

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fail_attached_call)
    monkeypatch.setattr(main, "_api_post", fake_api_post)

    result = runner.invoke(app, ["delegate", "start", "--thread", "thread-1", "--json"])

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["status"] == "blocked"
    assert payload["reason_blocked"] == "dispatch_failed"
    assert payload["target_role"] == "reviewer"
    assert payload["resolved_agent"] == "alice"


def test_delegate_wait_outputs_resume_packet(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / "custom-runtime-data"
    _thread_store, thread = _seed_waiting_delegate_thread(data_dir)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        ["delegate", "wait", "--thread", thread["thread_id"], "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["status"] == "waiting_for_human"
    assert payload["thread"]["alias"] == thread["thread_id"]
    assert payload["protocol"]["phase"] == "review"
    assert payload["resume"]["target_role"] == "reviewer"
    assert payload["resume"]["resolved_agent"] == "alice"
    assert payload["resume"]["required_action"] == "record_outcome"
    assert payload["resume"]["why_now"] == "phase requirements are met and a human outcome is required to continue"
    assert payload["resume"]["token"]
    assert "delegate respond" in payload["resume"]["suggested_next_command"]


def test_delegate_respond_reenters_loop_after_outcome(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / "custom-runtime-data"
    thread_store, thread = _seed_waiting_delegate_thread(data_dir)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "delegate",
            "respond",
            "--thread",
            thread["thread_id"],
            "--outcome",
            "retry",
            "--summary",
            "Need one more review pass.",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["status"] == "running"
    assert payload["current_phase"] == "review"
    assert payload["current_cycle_index"] == 2
    assert payload["loop_iteration"] == 2
    assert payload["resolved_agent"] == "alice"

    inbox = thread_store.list_inbox(thread["thread_id"], "alice")
    assert len(inbox) == 1
    assert "Need one more review pass." in inbox[0]["_content"]


def test_delegate_wait_and_respond_use_attached_api_when_attached(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    calls: list[tuple[str, object]] = []

    def fake_attached_get(path: str, params: dict | None = None) -> dict:
        calls.append((path, params))
        return {
            "status": "waiting_for_human",
            "thread": {"id": "thread-1", "alias": "thread-1", "topic": "Delegate wait thread"},
            "protocol": {"name": "delegate-wait", "phase": "review"},
            "resume": {
                "token": "resume-token",
                "target_role": "reviewer",
                "resolved_agent": "alice",
                "required_action": "record_outcome",
                "why_now": "phase requirements are met and a human outcome is required to continue",
                "suggested_next_command": "btwin delegate respond --thread thread-1 --outcome <retry|accept>",
            },
        }

    def fake_attached_post(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "thread_id": "thread-1",
            "status": "running",
            "current_phase": "review",
            "current_cycle_index": 2,
            "loop_iteration": 2,
            "resolved_agent": "alice",
        }

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)
    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_post)

    wait_result = runner.invoke(app, ["delegate", "wait", "--thread", "thread-1", "--json"])
    assert wait_result.exit_code == 0, wait_result.output
    assert _parse_json_output(wait_result.output)["resume"]["token"] == "resume-token"

    respond_result = runner.invoke(
        app,
        [
            "delegate",
            "respond",
            "--thread",
            "thread-1",
            "--outcome",
            "retry",
            "--summary",
            "Need one more review pass.",
            "--json",
        ],
    )
    assert respond_result.exit_code == 0, respond_result.output
    assert _parse_json_output(respond_result.output)["status"] == "running"

    assert calls == [
        ("/api/threads/thread-1/delegate/wait", None),
        (
            "/api/threads/thread-1/delegate/respond",
            {"outcome": "retry", "summary": "Need one more review pass."},
        ),
    ]


def test_delegate_resume_uses_attached_api_when_attached(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    calls: list[tuple[str, object]] = []

    def fake_attached_post(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "thread_id": "thread-1",
            "status": "running",
            "resolved_agent": "alice",
            "runtime_ensured": True,
            "pending_replayed": 1,
        }

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_post)

    result = runner.invoke(app, ["delegate", "resume", "--thread", "thread-1", "--json"])

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["runtime_ensured"] is True
    assert payload["pending_replayed"] == 1
    assert calls == [("/api/threads/thread-1/delegate/resume", {})]


def test_delegate_stop_marks_state_completed(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / "custom-runtime-data"
    _thread_store, thread = _seed_waiting_delegate_thread(data_dir)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        ["delegate", "stop", "--thread", thread["thread_id"], "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["status"] == "completed"
    assert payload["stop_reason"] == "stopped_by_operator"
