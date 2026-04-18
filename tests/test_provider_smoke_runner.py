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
from tests.protocol_scenario_matrix import get_scenario, scenario_protocol_definition


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


def _thread_watch_payload(
    provider_smoke_env: dict[str, str],
    thread_id: str,
    *,
    limit: int = 10,
) -> dict[str, object]:
    return _run_btwin(
        provider_smoke_env,
        "thread",
        "watch",
        thread_id,
        "--limit",
        str(limit),
        "--json",
    )


def _latest_trace_row(
    trace_payload: dict[str, object],
    *,
    kind: str,
) -> dict[str, object]:
    trace = trace_payload.get("trace")
    assert isinstance(trace, list), trace_payload
    for row in reversed(trace):
        if isinstance(row, dict) and row.get("kind") == kind:
            return row
    raise AssertionError(f"no trace row found for kind={kind}: {trace_payload}")


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


def _provider_review_retry_protocol_definition(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": "Repeat the same phase until accepted.",
        "outcomes": ["retry", "accept"],
        "guard_sets": [
            {
                "name": "review-default",
                "guards": [
                    "phase_actor_eligibility",
                    "direct_target_eligibility",
                ],
            }
        ],
        "phases": [
            {
                "name": "review",
                "description": "Review and revise the work.",
                "actions": ["contribute"],
                "template": [{"section": "completed", "required": True}],
                "guard_set": "review-default",
                "gate": "review-gate",
                "outcome_policy": "review-outcomes",
                "procedure": [
                    {
                        "role": "reviewer",
                        "action": "review",
                        "guidance": "Review the current implementation state.",
                    },
                    {
                        "role": "implementer",
                        "action": "revise",
                        "guidance": "Implement revisions from review feedback.",
                    },
                ],
            },
            {
                "name": "decision",
                "description": "Record final acceptance.",
                "actions": ["decide"],
            },
        ],
        "gates": [
            {
                "name": "review-gate",
                "routes": [
                    {"outcome": "retry", "target_phase": "review"},
                    {"outcome": "accept", "target_phase": "decision"},
                ],
            }
        ],
        "outcome_policies": [
            {
                "name": "review-outcomes",
                "emitters": ["reviewer", "user"],
                "actions": ["decide"],
                "outcomes": ["retry", "accept"],
            }
        ],
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


def test_provider_smoke_compiled_outcome_policy_hints_visible_on_next_and_apply_next(provider_smoke_env) -> None:
    protocol_name = "provider-smoke-review-retry"
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name=protocol_name,
        protocol_definition=_provider_review_retry_protocol_definition(protocol_name),
        participants=("alice",),
    )
    thread_id = str(state["thread_id"])

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
        "## completed\nNeeds another pass.\n",
        "--tldr",
        "review retry",
        "--json",
    )

    plan = _run_btwin(
        provider_smoke_env,
        "protocol",
        "next",
        "--thread",
        thread_id,
        "--outcome",
        "retry",
        "--json",
    )

    assert plan["current_phase"] == "review"
    assert plan["requested_outcome"] == "retry"
    assert plan["next_phase"] == "review"
    assert plan["suggested_action"] == "advance_phase"
    assert plan["valid_outcomes"] == ["retry", "accept"]
    assert plan["guard_set"] == "review-default"
    assert plan["declared_guards"] == [
        "phase_actor_eligibility",
        "direct_target_eligibility",
    ]
    assert plan["outcome_policy"] == "review-outcomes"
    assert plan["outcome_emitters"] == ["reviewer", "user"]
    assert plan["outcome_actions"] == ["decide"]
    assert plan["policy_outcomes"] == ["retry", "accept"]
    assert "baseline runtime guard remains always-on" in plan["hint"]

    applied = _run_btwin(
        provider_smoke_env,
        "protocol",
        "apply-next",
        "--thread",
        thread_id,
        "--outcome",
        "retry",
        "--json",
    )

    assert applied["applied"] is True
    assert applied["thread"]["current_phase"] == plan["next_phase"]
    assert applied["suggested_action"] == "advance_phase"
    assert applied["cycle"]["cycle_index"] == 2
    assert applied["cycle"]["phase_name"] == "review"
    assert applied["context_core"]["current_cycle_index"] == 2
    assert applied["context_core"]["last_cycle_outcome"] == "retry"
    assert applied["context_core"]["guard_set"] == plan["guard_set"]
    assert applied["context_core"]["declared_guards"] == plan["declared_guards"]
    assert applied["context_core"]["outcome_policy"] == plan["outcome_policy"]
    assert applied["context_core"]["outcome_emitters"] == plan["outcome_emitters"]
    assert applied["context_core"]["outcome_actions"] == plan["outcome_actions"]
    assert applied["context_core"]["policy_outcomes"] == plan["policy_outcomes"]
    assert applied["context_core"]["current_step_key"] == "review"

    trace_payload = _thread_watch_payload(provider_smoke_env, thread_id, limit=10)
    gate_row = _latest_trace_row(trace_payload, kind="gate")
    assert gate_row["outcome_policy"] == plan["outcome_policy"]
    assert gate_row["outcome_emitters"] == plan["outcome_emitters"]
    assert gate_row["outcome_actions"] == plan["outcome_actions"]
    assert gate_row["policy_outcomes"] == plan["policy_outcomes"]


def test_provider_smoke_stop_block_uses_shared_scenario_fixture(provider_smoke_env) -> None:
    scenario = get_scenario("blocked_stop_missing_contribution")
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name=scenario.protocol_name,
        protocol_definition=scenario_protocol_definition("blocked_stop_missing_contribution"),
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
    assert scenario.preview_status == "note"
    assert scenario.live_smoke_required is True
    assert "baseline runtime guard remains always-on" in payload["overlay"]


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
    scenario = get_scenario("close_path")
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name=scenario.protocol_name,
        protocol_definition=scenario_protocol_definition("close_path"),
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


def test_provider_smoke_close_path_thread_watch_gate_matches_preview(provider_smoke_env) -> None:
    scenario = get_scenario("close_path")
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name=scenario.protocol_name,
        protocol_definition=scenario_protocol_definition("close_path"),
        participants=("alice",),
    )
    thread_id = str(state["thread_id"])

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
        "## completed\nReady to close.\n",
        "--tldr",
        "close path ready",
        "--json",
    )
    preview = _run_btwin(
        provider_smoke_env,
        "protocol",
        "next",
        "--thread",
        thread_id,
        "--outcome",
        str(scenario.outcome),
        "--json",
    )
    applied = _run_btwin(
        provider_smoke_env,
        "protocol",
        "apply-next",
        "--thread",
        thread_id,
        "--outcome",
        str(scenario.outcome),
        "--summary",
        "Closed from provider smoke",
        "--decision",
        "close",
        "--json",
    )
    trace_payload = _thread_watch_payload(provider_smoke_env, thread_id, limit=10)
    gate_row = _latest_trace_row(trace_payload, kind="gate")

    assert scenario.preview_status == "valid"
    assert scenario.live_smoke_required is True
    assert preview["requested_outcome"] == scenario.outcome
    assert preview["next_phase"] == scenario.target_phase
    assert applied["applied"] is True
    assert applied["suggested_action"] == "advance_phase"
    assert applied["thread"]["current_phase"] == scenario.target_phase
    assert gate_row["outcome"] == scenario.outcome
    assert gate_row["gate_key"] == scenario.gate_key
    assert gate_row["target_phase"] == scenario.target_phase
    assert gate_row["procedure_key"] == scenario.procedure_key


def test_provider_smoke_attach_seed_first_cycle_uses_real_phase_cycle_state(provider_smoke_env) -> None:
    scenario = get_scenario("attach_seed_first_cycle")
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name=scenario.protocol_name,
        protocol_definition=scenario_protocol_definition("attach_seed_first_cycle"),
        participants=("alice",),
    )
    thread_id = str(state["thread_id"])

    phase_cycle_response = httpx.get(
        f'{provider_smoke_env["BTWIN_API_URL"]}/api/threads/{thread_id}/phase-cycle',
        timeout=5.0,
    )
    phase_cycle_response.raise_for_status()
    phase_cycle = phase_cycle_response.json()
    trace_payload = _thread_watch_payload(provider_smoke_env, thread_id, limit=10)
    watch_phase_cycle = trace_payload.get("phase_cycle")

    assert scenario.preview_status == "valid"
    assert scenario.live_smoke_required is True
    assert phase_cycle["state"]["cycle_index"] == 1
    assert phase_cycle["state"]["phase_name"] == "review"
    assert phase_cycle["context_core"]["current_step_alias"] == "Review"
    assert phase_cycle["context_core"]["next_expected_role"] == "reviewer"
    assert phase_cycle["visual"]["procedure"][0]["key"] == scenario.procedure_key
    assert phase_cycle["visual"]["gates"][0]["key"] == scenario.gate_key
    assert phase_cycle["visual"]["gates"][0]["target_phase"] == scenario.target_phase
    assert isinstance(watch_phase_cycle, dict), trace_payload
    assert watch_phase_cycle["state"]["cycle_index"] == 1
    assert "synthetic" not in watch_phase_cycle


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
    assert "baseline runtime guard remains always-on" in payload["overlay"]
    assert "no protocol-declared guard set" in payload["overlay"]


def test_provider_smoke_protocol_guard_set_visible_but_baseline_guards_still_enforced(provider_smoke_env) -> None:
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name="provider-smoke-guard-set",
        protocol_definition={
            "name": "provider-smoke-guard-set",
            "guard_sets": [
                {
                    "name": "review-default",
                    "guards": [
                        "contribution_required",
                        "transition_precondition",
                    ],
                }
            ],
            "phases": [
                {
                    "name": "review",
                    "actions": ["contribute"],
                    "guard_set": "review-default",
                    "template": [{"section": "completed", "required": True}],
                },
                {
                    "name": "decision",
                    "actions": ["decide"],
                    "decided_by": "user",
                },
            ],
        },
    )

    plan = _run_btwin(
        provider_smoke_env,
        "protocol",
        "next",
        "--thread",
        state["thread_id"],
        "--json",
    )

    assert plan["guard_set"] == "review-default"
    assert plan["declared_guards"] == ["contribution_required", "transition_precondition"]
    assert "baseline runtime guard" in plan["hint"]

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

    assert payload["reason"] == "missing_contribution"
    assert payload["details"]["guard_source"] == "baseline"
    assert payload["details"]["phase_guard_set"] == "review-default"
    assert payload["details"]["declared_guards"] == [
        "contribution_required",
        "transition_precondition",
    ]
    assert "baseline runtime guard remains always-on" in payload["overlay"]
    assert "protocol-declared guards are additive" in payload["overlay"]


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
    scenario = get_scenario("retry_same_phase")
    state = _setup_provider_smoke_thread(
        provider_smoke_env,
        protocol_name=scenario.protocol_name,
        protocol_definition=scenario_protocol_definition("retry_same_phase"),
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
    assert phase_cycle_payload["visual"]["gates"][0] == scenario.visual_gates[0].as_dict()
    assert phase_cycle_payload["visual"]["gates"][1] == scenario.visual_gates[1].as_dict()
    assert scenario.gate_key == "retry-loop"
    assert scenario.procedure_key == "review-pass"
    assert scenario.outcome == "retry"
    assert scenario.target_phase == "review"

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
