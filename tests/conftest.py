from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import yaml

CLI_SMOKE_FILES = {
    "test_attached_helper_smoke_script.py",
}

INTEGRATION_FILES = {
    "test_bootstrap_isolated_attached_env.py",
    "test_codex_session_health_script.py",
}


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "unit: fast default tests that exclude integration, cli smoke, and provider smoke",
    )
    config.addinivalue_line("markers", "integration: integration-level tests")
    config.addinivalue_line("markers", "cli_smoke: end-to-end CLI smoke tests")
    config.addinivalue_line(
        "markers",
        "provider_smoke: provider-attached smoke tests that require explicit opt-in",
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    try:
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _parse_env_file(env_path: Path) -> dict[str, str]:
    exports: dict[str, str] = {}
    pattern = re.compile(r'^export ([A-Z0-9_]+)="(.*)"$')
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        match = pattern.match(line)
        if match:
            exports[match.group(1)] = match.group(2)
    return exports


def _wait_for_api(api_url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{api_url}/api/sessions/status", timeout=1.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for API at {api_url}")


def pytest_collection_modifyitems(config, items) -> None:
    del config
    for item in items:
        marker_names = {mark.name for mark in item.iter_markers()}
        if {"unit", "integration", "cli_smoke", "provider_smoke"} & marker_names:
            continue

        file_name = Path(str(item.fspath)).name
        if file_name in CLI_SMOKE_FILES:
            item.add_marker(pytest.mark.cli_smoke)
        elif file_name in INTEGRATION_FILES:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)


@pytest.fixture
def provider_smoke_env(tmp_path: Path) -> dict[str, str]:
    repo_root = _repo_root()
    script_path = repo_root / "scripts" / "bootstrap_isolated_attached_env.sh"
    root_dir = tmp_path / "provider-smoke-env"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    port = str(_find_free_port())
    provider_auth_home = os.environ.get("BTWIN_PROVIDER_AUTH_HOME") or os.environ.get("HOME") or str(tmp_path)

    env = os.environ.copy()
    env["HOME"] = provider_auth_home

    start = subprocess.run(
        [
            "bash",
            str(script_path),
            "start",
            "--root",
            str(root_dir),
            "--project-root",
            str(project_root),
            "--project",
            "provider-smoke-test",
            "--port",
            port,
            "--skip-server",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert start.returncode == 0, start.stderr or start.stdout

    config_path = root_dir / "config.yaml"
    config_payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    runtime_payload = dict(config_payload.get("runtime") or {})
    runtime_payload["mode"] = "attached"
    runtime_payload["persistent_transport_enabled"] = True
    runtime_payload["persistent_transport_providers"] = ["codex"]
    runtime_payload["persistent_transport_auto_fallback"] = True
    config_payload["runtime"] = runtime_payload
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

    env_payload = _parse_env_file(root_dir / "env.sh")
    env_payload["provider_surface"] = os.environ.get("BTWIN_PROVIDER_SURFACE", "app-server")
    env_payload["provider_continuity"] = os.environ.get("BTWIN_PROVIDER_CONTINUITY", "long-term")
    env_payload["provider_model"] = os.environ.get("BTWIN_PROVIDER_MODEL", "gpt-5.4-mini")
    env_payload["provider_auth_home"] = provider_auth_home
    serve_stdout = root_dir / "logs" / "provider-smoke-serve-api.stdout.log"
    serve_stderr = root_dir / "logs" / "provider-smoke-serve-api.stderr.log"
    serve_stdout.parent.mkdir(parents=True, exist_ok=True)
    serve_process = subprocess.Popen(
        [
            env_payload["BTWIN_TEST_BTWIN_BIN"],
            "serve-api",
            "--port",
            port,
        ],
        cwd=repo_root,
        env={
            **os.environ,
            "HOME": provider_auth_home,
            "BTWIN_CONFIG_PATH": env_payload["BTWIN_CONFIG_PATH"],
            "BTWIN_DATA_DIR": env_payload["BTWIN_DATA_DIR"],
            "BTWIN_API_URL": env_payload["BTWIN_API_URL"],
        },
        stdout=serve_stdout.open("w", encoding="utf-8"),
        stderr=serve_stderr.open("w", encoding="utf-8"),
        text=True,
    )
    env_payload["serve_api_pid"] = str(serve_process.pid)
    _wait_for_api(env_payload["BTWIN_API_URL"], timeout_seconds=12.0)

    try:
        yield env_payload
    finally:
        if serve_process.poll() is None:
            serve_process.terminate()
            try:
                serve_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                serve_process.kill()
                serve_process.wait(timeout=5)
        subprocess.run(
            [
                "bash",
                str(script_path),
                "stop",
                "--root",
                str(root_dir),
                "--port",
                port,
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
