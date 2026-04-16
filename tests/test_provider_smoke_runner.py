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


def _run_btwin(provider_smoke_env: dict[str, str], *args: str) -> dict:
    result = subprocess.run(
        [provider_smoke_env["BTWIN_TEST_BTWIN_BIN"], *args],
        cwd=Path(provider_smoke_env["BTWIN_TEST_ROOT"]),
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
    assert result.returncode == 0, result.stderr or result.stdout
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


def _write_protocol(provider_smoke_env: dict[str, str]) -> None:
    protocols_dir = Path(provider_smoke_env["BTWIN_DATA_DIR"]) / "protocols"
    protocols_dir.mkdir(parents=True, exist_ok=True)
    protocol_path = protocols_dir / "provider-smoke.yaml"
    protocol_path.write_text(
        yaml.safe_dump(
            {
                "name": "provider-smoke",
                "phases": [
                    {
                        "name": "context",
                        "actions": ["contribute", "discuss"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


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


def _run_scripted_provider_smoke(provider_smoke_env: dict[str, str]) -> dict[str, object]:
    preflight = _provider_preflight(provider_smoke_env)
    _write_protocol(provider_smoke_env)

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
    thread = _run_btwin(
        provider_smoke_env,
        "thread",
        "create",
        "--topic",
        "Provider smoke scripted",
        "--protocol",
        "provider-smoke",
        "--participant",
        "alice",
        "--participant",
        "user",
        "--json",
    )
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

    def runtime_status():
        payload = httpx.get(
            f'{provider_smoke_env["BTWIN_API_URL"]}/api/agent-runtime-status',
            timeout=5.0,
        ).json()
        for session in payload.get("agents", {}).get("alice", []):
            if session.get("thread_id") == thread_id:
                return session
        return None

    attached_status = _wait_for(
        runtime_status,
        timeout_seconds=60.0,
        step=1.0,
    )
    if attached_status is None:
        pytest.skip("Timed out waiting for attached Codex runtime session")

    def thread_messages():
        return httpx.get(
            f'{provider_smoke_env["BTWIN_API_URL"]}/api/threads/{thread_id}/messages',
            timeout=5.0,
        ).json()

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
            if (status := runtime_status()) and status.get("status") in {"received", "done"}
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
                for message in thread_messages()
                if message.get("from") == "alice" and message.get("_content") == "PROVIDER_SMOKE_OK"
            ),
            None,
        ),
        timeout_seconds=60.0,
        step=1.0,
    )
    assert exact_reply is not None, "Timed out waiting for live provider session to persist the exact reply"

    final_status = runtime_status() or attached_status
    all_messages = thread_messages()
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
