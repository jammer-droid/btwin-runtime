from pathlib import Path
import asyncio
import json
import os
import subprocess
import time

import httpx
import pytest
import yaml

from btwin_core.prototypes.persistent_sessions.harness import run_provider_scenario


pytestmark = pytest.mark.provider_smoke


def _run_btwin_result(provider_smoke_env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [provider_smoke_env["BTWIN_TEST_BTWIN_BIN"], *args],
        cwd=Path(provider_smoke_env["project_root"]),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "BTWIN_CONFIG_PATH": provider_smoke_env["BTWIN_CONFIG_PATH"],
            "BTWIN_DATA_DIR": provider_smoke_env["BTWIN_DATA_DIR"],
            "BTWIN_API_URL": provider_smoke_env["BTWIN_API_URL"],
        },
        check=False,
    )
    return result


def _run_btwin(
    provider_smoke_env: dict[str, str],
    *args: str,
    expected_returncode: int = 0,
) -> dict:
    result = _run_btwin_result(provider_smoke_env, *args)
    assert result.returncode == expected_returncode, result.stderr or result.stdout
    return json.loads(result.stdout)


def _wait_for(condition, *, timeout_seconds: float, step: float = 0.5):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(step)
    return None


def _provider_run_dir(provider_smoke_env: dict[str, str]) -> Path:
    run_dir = os.environ.get("BTWIN_TEST_RUN_DIR")
    if run_dir:
        path = Path(run_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = Path(provider_smoke_env["BTWIN_TEST_ROOT"]) / "provider-smoke-artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_protocol(provider_smoke_env: dict[str, str], protocol_definition: dict[str, object]) -> None:
    protocols_dir = Path(provider_smoke_env["BTWIN_DATA_DIR"]) / "protocols"
    protocols_dir.mkdir(parents=True, exist_ok=True)
    protocol_name = str(protocol_definition["name"])
    protocol_path = protocols_dir / f"{protocol_name}.yaml"
    protocol_path.write_text(
        yaml.safe_dump(protocol_definition, sort_keys=False),
        encoding="utf-8",
    )


def _api_post(
    provider_smoke_env: dict[str, str],
    path: str,
    payload: dict[str, object],
    *,
    expected_status: int = 200,
) -> dict:
    response = httpx.post(
        f'{provider_smoke_env["BTWIN_API_URL"]}{path}',
        json=payload,
        timeout=5.0,
    )
    assert response.status_code == expected_status, response.text
    return response.json()


def _runtime_status(provider_smoke_env: dict[str, str], thread_id: str, *, agent_name: str = "alice"):
    payload = httpx.get(
        f'{provider_smoke_env["BTWIN_API_URL"]}/api/agent-runtime-status',
        timeout=5.0,
    ).json()
    for session in payload.get("agents", {}).get(agent_name, []):
        if session.get("thread_id") == thread_id:
            return session
    return None


def _thread_messages(provider_smoke_env: dict[str, str], thread_id: str) -> list[dict]:
    return httpx.get(
        f'{provider_smoke_env["BTWIN_API_URL"]}/api/threads/{thread_id}/messages',
        timeout=5.0,
    ).json()


def _provider_preflight(provider_smoke_env: dict[str, str]) -> dict[str, object]:
    result = asyncio.run(
        run_provider_scenario(
            provider="codex-app-server",
            provider_command="codex",
            model=provider_smoke_env["provider_model"],
        )
    )
    payload = {
        "status": result.status,
        "provider": result.provider,
        "capability": result.capability,
        "continuity_mode": result.continuity_mode,
        "launch_strategy": result.launch_strategy,
        "error": result.error,
        "requested_model": result.start_metadata.get("requested_model"),
        "effective_model": result.start_metadata.get("effective_model"),
        "start_metadata": result.start_metadata,
    }
    (_provider_run_dir(provider_smoke_env) / "provider-session.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if result.status != "works":
        pytest.skip(f"Codex app-server preflight unavailable: {result.error or result.status}")
    return payload


def _setup_provider_smoke_thread(
    provider_smoke_env: dict[str, str],
    *,
    protocol_name: str,
    protocol_definition: dict[str, object],
    participants: tuple[str, ...] = ("alice", "user"),
) -> dict[str, object]:
    preflight = _provider_preflight(provider_smoke_env)
    _write_protocol(provider_smoke_env, protocol_definition)

    _run_btwin(
        provider_smoke_env,
        "agent",
        "create",
        "alice",
        "--provider",
        "codex",
        "--role",
        "implementer",
        "--model",
        provider_smoke_env["provider_model"],
        "--json",
    )

    thread_args = [
        "thread",
        "create",
        "--topic",
        f"{protocol_name} thread",
        "--protocol",
        protocol_name,
        "--json",
    ]
    for participant in participants:
        thread_args.extend(["--participant", participant])
    thread = _run_btwin(provider_smoke_env, *thread_args)
    thread_id = thread["thread_id"]

    _run_btwin(
        provider_smoke_env,
        "live",
        "attach",
        "--thread",
        thread_id,
        "--agent",
        "alice",
        "--json",
    )

    attached_status = _wait_for(
        lambda: _runtime_status(provider_smoke_env, thread_id),
        timeout_seconds=60.0,
        step=1.0,
    )
    if attached_status is None:
        pytest.skip("Timed out waiting for attached Codex runtime session")

    return {
        "thread_id": thread_id,
        "preflight": preflight,
        "attached_status": attached_status,
    }


def _run_scripted_provider_smoke(provider_smoke_env: dict[str, str]) -> dict[str, object]:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke",
        protocol_definition={
            "name": "provider-smoke",
            "phases": [
                {
                    "name": "context",
                    "actions": ["contribute", "discuss"],
                }
            ],
        },
    )
    preflight = state["preflight"]
    thread_id = str(state["thread_id"])
    attached_status = state["attached_status"]

    _run_btwin(
        provider_smoke_env,
        "thread",
        "send-message",
        "--thread",
        thread_id,
        "--from",
        "user",
        "--content",
        "Please reply with exactly: PROVIDER_SMOKE_OK",
        "--tldr",
        "provider smoke exact reply request",
        "--delivery-mode",
        "direct",
        "--target",
        "alice",
        "--json",
    )

    delivery_state = _wait_for(
        lambda: (
            status.get("status")
            if (status := _runtime_status(provider_smoke_env, thread_id)) and status.get("status") in {"received", "done"}
            else None
        ),
        timeout_seconds=60.0,
        step=1.0,
    )
    assert delivery_state is not None, "Timed out waiting for live provider session to receive the direct prompt"

    exact_reply = _wait_for(
        lambda: next(
            (
                message
                for message in _thread_messages(provider_smoke_env, thread_id)
                if message.get("from") == "alice" and message.get("_content") == "PROVIDER_SMOKE_OK"
            ),
            None,
        ),
        timeout_seconds=60.0,
        step=1.0,
    )
    assert exact_reply is not None, "Timed out waiting for live provider session to persist the exact reply"

    final_status = _runtime_status(provider_smoke_env, thread_id) or attached_status
    all_messages = _thread_messages(provider_smoke_env, thread_id)
    runtime_events_path = Path(provider_smoke_env["BTWIN_DATA_DIR"]) / "logs" / "runtime-events.jsonl"
    runtime_events = []
    if runtime_events_path.exists():
        runtime_events = [
            json.loads(line)
            for line in runtime_events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    payload = {
        "thread_id": thread_id,
        "requested_model": preflight["requested_model"],
        "effective_model": preflight["effective_model"],
        "transport_mode": final_status["transport_mode"],
        "primary_transport_mode": final_status["primary_transport_mode"],
        "provider_session_id": final_status.get("provider_session_id"),
        "delivery_state": delivery_state,
        "exact_reply_observed": bool(exact_reply),
        "messages": all_messages,
        "runtime_events": runtime_events,
    }
    run_dir = _provider_run_dir(provider_smoke_env)
    (run_dir / "thread-state.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if runtime_events_path.exists():
        (run_dir / "runtime-events.jsonl").write_text(
            runtime_events_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return payload


def test_provider_smoke_fixture_exports_isolated_env(provider_smoke_env) -> None:
    assert provider_smoke_env["BTWIN_API_URL"].startswith("http://127.0.0.1:")
    assert provider_smoke_env["BTWIN_DATA_DIR"]
    assert Path(provider_smoke_env["BTWIN_TEST_ROOT"]).exists()

    response = httpx.get(
        f'{provider_smoke_env["BTWIN_API_URL"]}/api/sessions/status',
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json()
    assert "active" in payload
    assert "locale" in payload


def test_provider_smoke_fixture_uses_default_provider_profile(provider_smoke_env) -> None:
    assert provider_smoke_env["provider_surface"] == "app-server"
    assert provider_smoke_env["provider_continuity"] == "long-term"
    assert provider_smoke_env["provider_model"] == "gpt-5.4-mini"


def test_provider_smoke_runs_scripted_thread_flow(provider_smoke_env) -> None:
    result = _run_scripted_provider_smoke(provider_smoke_env)

    assert result["requested_model"] == "gpt-5.4-mini"
    assert result["transport_mode"] == "live_process_transport"
    assert result["primary_transport_mode"] == "live_process_transport"
    assert result["provider_session_id"]
    assert result["thread_id"]
    assert result["runtime_events"]
    assert result["delivery_state"] in {"received", "done"}
    assert any(
        message["from"] == "user" and message["_content"] == "Please reply with exactly: PROVIDER_SMOKE_OK"
        for message in result["messages"]
    )
    assert any(
        message["from"] == "alice"
        and message["_content"] == "PROVIDER_SMOKE_OK"
        and message.get("message_phase") == "final_answer"
        and message.get("state_affecting") is True
        for message in result["messages"]
    )
    assert any(event["eventType"] == "runtime_session_started" for event in result["runtime_events"])


def test_provider_smoke_apply_next_reports_missing_contribution_hint(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-hint",
        protocol_definition={
            "name": "provider-smoke-hint",
            "phases": [
                {
                    "name": "context",
                    "actions": ["contribute"],
                    "template": [{"section": "background", "required": True}],
                }
            ],
        },
    )

    payload = _run_btwin(
        provider_smoke_env,
        "protocol",
        "apply-next",
        "--thread",
        state["thread_id"],
        "--json",
    )

    assert payload["applied"] is False
    assert payload["suggested_action"] == "submit_contribution"
    assert "btwin contribution submit" in payload["hint"]


def test_provider_smoke_close_gate_blocks_before_phase_advance(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-close-gate",
        protocol_definition={
            "name": "provider-smoke-close-gate",
            "phases": [
                {
                    "name": "context",
                    "actions": ["contribute"],
                    "template": [{"section": "background", "required": True}],
                },
                {
                    "name": "discussion",
                    "actions": ["discuss"],
                },
            ],
        },
        participants=("alice",),
    )

    _run_btwin(
        provider_smoke_env,
        "contribution",
        "submit",
        "--thread",
        state["thread_id"],
        "--agent",
        "alice",
        "--phase",
        "context",
        "--content",
        "## background\nReady for discussion.\n",
        "--tldr",
        "context ready",
        "--json",
    )

    response = _api_post(
        provider_smoke_env,
        f"/api/threads/{state['thread_id']}/close",
        {"summary": "done", "decision": "merge"},
        expected_status=409,
    )

    detail = response["detail"]
    assert detail["error"] == "thread_not_closable_from_phase"
    assert "protocol apply-next" in detail["hint"]


def test_provider_smoke_contribution_gate_blocks_non_user_decision_actor(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-decision-gate",
        protocol_definition={
            "name": "provider-smoke-decision-gate",
            "phases": [
                {
                    "name": "decision",
                    "actions": ["decide"],
                    "decided_by": "user",
                    "template": [{"section": "agreed_points", "required": True}],
                }
            ],
        },
    )

    response = _api_post(
        provider_smoke_env,
        f"/api/threads/{state['thread_id']}/contributions",
        {
            "agentName": "alice",
            "phase": "decision",
            "content": "## agreed_points\nShip it.\n",
            "tldr": "decision from alice",
        },
        expected_status=409,
    )

    detail = response["detail"]
    assert detail["error"] == "actor_not_allowed_for_phase"
    assert "user" in detail["hint"]


def test_provider_smoke_contribution_gate_blocks_phase_mismatch(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-phase-mismatch",
        protocol_definition={
            "name": "provider-smoke-phase-mismatch",
            "phases": [
                {
                    "name": "context",
                    "actions": ["contribute"],
                    "template": [{"section": "background", "required": True}],
                },
                {
                    "name": "decision",
                    "actions": ["decide"],
                    "decided_by": "user",
                    "template": [{"section": "agreed_points", "required": True}],
                },
            ],
        },
    )

    response = _api_post(
        provider_smoke_env,
        f"/api/threads/{state['thread_id']}/contributions",
        {
            "agentName": "alice",
            "phase": "decision",
            "content": "## agreed_points\nShip it.\n",
            "tldr": "decision too early",
        },
        expected_status=409,
    )

    detail = response["detail"]
    assert detail["error"] == "phase_mismatch"
    assert detail["details"]["current_phase"] == "context"
    assert "btwin contribution submit" in detail["hint"]


def test_provider_smoke_contribution_gate_blocks_non_contribution_phase(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-phase-action-gate",
        protocol_definition={
            "name": "provider-smoke-phase-action-gate",
            "phases": [
                {
                    "name": "discussion",
                    "actions": ["discuss"],
                }
            ],
        },
    )

    response = _api_post(
        provider_smoke_env,
        f"/api/threads/{state['thread_id']}/contributions",
        {
            "agentName": "alice",
            "phase": "discussion",
            "content": "## notes\nNeed another pass.\n",
            "tldr": "not a contribution phase",
        },
        expected_status=409,
    )

    detail = response["detail"]
    assert detail["error"] == "phase_action_not_allowed"
    assert "send-message" in detail["hint"]


def test_provider_smoke_direct_message_gate_blocks_non_discussion_phase(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-direct-phase-gate",
        protocol_definition={
            "name": "provider-smoke-direct-phase-gate",
            "phases": [
                {
                    "name": "context",
                    "actions": ["contribute"],
                    "template": [{"section": "background", "required": True}],
                }
            ],
        },
    )

    response = _api_post(
        provider_smoke_env,
        f"/api/threads/{state['thread_id']}/messages",
        {
            "fromAgent": "user",
            "content": "Please respond directly.",
            "tldr": "direct chat not allowed yet",
            "deliveryMode": "direct",
            "targetAgents": ["alice"],
        },
        expected_status=409,
    )

    detail = response["detail"]
    assert detail["error"] == "direct_message_not_allowed_in_phase"
    assert "btwin contribution submit" in detail["hint"]


def test_provider_smoke_direct_message_gate_blocks_ineligible_target(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-direct-target-gate",
        protocol_definition={
            "name": "provider-smoke-direct-target-gate",
            "phases": [
                {
                    "name": "discussion",
                    "actions": ["discuss"],
                }
            ],
        },
    )

    response = _api_post(
        provider_smoke_env,
        f"/api/threads/{state['thread_id']}/messages",
        {
            "fromAgent": "alice",
            "content": "Looping this back to myself.",
            "tldr": "invalid direct target",
            "deliveryMode": "direct",
            "targetAgents": ["alice"],
        },
        expected_status=409,
    )

    detail = response["detail"]
    assert detail["error"] == "target_not_eligible_for_phase"
    assert "user" in detail["hint"]


def test_provider_smoke_close_gate_blocks_when_required_contributions_missing(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-close-missing",
        protocol_definition={
            "name": "provider-smoke-close-missing",
            "phases": [
                {
                    "name": "context",
                    "actions": ["contribute"],
                    "template": [{"section": "background", "required": True}],
                }
            ],
        },
        participants=("alice",),
    )

    response = _api_post(
        provider_smoke_env,
        f"/api/threads/{state['thread_id']}/close",
        {"summary": "done", "decision": "merge"},
        expected_status=409,
    )

    detail = response["detail"]
    assert detail["error"] == "phase_requirements_not_met"
    assert "btwin contribution submit" in detail["hint"]


def test_provider_smoke_workflow_hook_blocks_stop_without_required_contribution(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-stop-block",
        protocol_definition={
            "name": "provider-smoke-stop-block",
            "phases": [
                {
                    "name": "context",
                    "actions": ["contribute"],
                    "template": [{"section": "background", "required": True}],
                }
            ],
        },
        participants=("alice",),
    )

    payload = _run_btwin(
        provider_smoke_env,
        "workflow",
        "hook",
        "--event",
        "Stop",
        "--thread",
        state["thread_id"],
        "--agent",
        "alice",
        "--json",
        expected_returncode=2,
    )

    assert payload["event"] == "Stop"
    assert payload["decision"] == "block"
    assert payload["reason"] == "missing_contribution"
    assert payload["required_result_recorded"] is False


def test_provider_smoke_workflow_hook_allows_non_required_actor_in_user_decision_phase(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-stop-allow-nonrequired",
        protocol_definition={
            "name": "provider-smoke-stop-allow-nonrequired",
            "phases": [
                {
                    "name": "decision",
                    "actions": ["decide"],
                    "decided_by": "user",
                    "template": [{"section": "agreed_points", "required": True}],
                }
            ],
        },
    )

    payload = _run_btwin(
        provider_smoke_env,
        "workflow",
        "hook",
        "--event",
        "Stop",
        "--thread",
        state["thread_id"],
        "--agent",
        "alice",
        "--json",
    )

    assert payload["event"] == "Stop"
    assert payload["decision"] == "allow"
    assert payload["required_result_recorded"] is False


def test_attached_scenario_repeats_same_phase_across_multiple_cycles(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-repeat-cycle",
        protocol_definition={
            "name": "provider-smoke-repeat-cycle",
            "outcomes": ["retry", "accept"],
            "phases": [
                {
                    "name": "review",
                    "actions": ["contribute"],
                    "template": [{"section": "completed", "required": True}],
                    "procedure": [
                        {"key": "review-pass", "role": "reviewer", "action": "review", "alias": "Review"},
                        {"key": "revise-pass", "role": "implementer", "action": "revise", "alias": "Revise"},
                    ],
                }
            ],
            "transitions": [
                {"key": "retry-loop", "from": "review", "to": "review", "on": "retry", "alias": "Retry Gate"},
                {"key": "accept-loop", "from": "review", "to": "review", "on": "accept", "alias": "Accept Gate"},
            ],
        },
        participants=("alice",),
    )
    thread_id = str(state["thread_id"])

    blocked = _run_btwin(
        provider_smoke_env,
        "workflow",
        "hook",
        "--event",
        "Stop",
        "--thread",
        thread_id,
        "--agent",
        "alice",
        "--json",
        expected_returncode=2,
    )
    assert blocked["decision"] == "block"

    mailbox_before = httpx.get(
        f'{provider_smoke_env["BTWIN_API_URL"]}/api/system-mailbox',
        params={"threadId": thread_id, "limit": 10},
        timeout=5.0,
    )
    mailbox_before.raise_for_status()
    assert mailbox_before.json()["reports"] == []

    for cycle_index in (1, 2):
        _run_btwin(
            provider_smoke_env,
            "contribution",
            "submit",
            "--thread",
            thread_id,
            "--agent",
            "alice",
            "--phase",
            "review",
            "--content",
            f"## completed\nCycle {cycle_index} ready for another pass.\n",
            "--tldr",
            f"review cycle {cycle_index}",
            "--json",
        )
        payload = _run_btwin(
            provider_smoke_env,
            "protocol",
            "apply-next",
            "--thread",
            thread_id,
            "--outcome",
            "retry",
            "--json",
        )
        assert payload["applied"] is True
        assert payload["thread"]["current_phase"] == "review"

    mailbox_response = httpx.get(
        f'{provider_smoke_env["BTWIN_API_URL"]}/api/system-mailbox',
        params={"threadId": thread_id, "limit": 10},
        timeout=5.0,
    )
    mailbox_response.raise_for_status()
    reports = mailbox_response.json()["reports"]

    assert len(reports) == 2
    assert all(report["report_type"] == "cycle_result" for report in reports)
    assert all(report["cycle_finished"] is True for report in reports)
    assert [report["cycle_index"] for report in reports] == [2, 1]
    assert [report["next_cycle_index"] for report in reports] == [3, 2]
    assert all(report["next_phase"] == "review" for report in reports)

    phase_cycle_response = httpx.get(
        f'{provider_smoke_env["BTWIN_API_URL"]}/api/threads/{thread_id}/phase-cycle',
        timeout=5.0,
    )
    phase_cycle_response.raise_for_status()
    phase_cycle_payload = phase_cycle_response.json()
    assert phase_cycle_payload["state"]["cycle_index"] == 3
    assert phase_cycle_payload["state"]["phase_name"] == "review"
    assert phase_cycle_payload["context_core"]["current_cycle_index"] == 3
    assert phase_cycle_payload["context_core"]["next_expected_action"] == "review"
    assert phase_cycle_payload["visual"]["procedure"][0] == {
        "key": "review-pass",
        "label": "Review",
        "status": "active",
    }
    assert phase_cycle_payload["visual"]["procedure"][1] == {
        "key": "revise-pass",
        "label": "Revise",
        "status": "pending",
    }
    assert phase_cycle_payload["visual"]["gates"][0] == {
        "key": "retry-loop",
        "label": "Retry Gate",
        "status": "completed",
        "target_phase": "review",
    }
    assert phase_cycle_payload["visual"]["gates"][1] == {
        "key": "accept-loop",
        "label": "Accept Gate",
        "status": "pending",
        "target_phase": "review",
    }

    hud_result = _run_btwin_result(provider_smoke_env, "hud", "--thread", thread_id, "--limit", "5")
    assert hud_result.returncode == 0, hud_result.stderr or hud_result.stdout
    assert "Protocol Progress" in hud_result.stdout
    assert "Active cycle: 3" in hud_result.stdout
    assert "Completed cycles: 2" in hud_result.stdout
    assert "Procedure" in hud_result.stdout
    assert "Review" in hud_result.stdout
    assert "Revise" in hud_result.stdout
    assert "Gates" in hud_result.stdout
    assert "Retry Gate" in hud_result.stdout
    assert "Accept Gate" in hud_result.stdout
