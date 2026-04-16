from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path

import pytest

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
    home_dir = tmp_path / "home"
    project_root.mkdir(parents=True, exist_ok=True)
    home_dir.mkdir(parents=True, exist_ok=True)
    port = str(_find_free_port())

    env = os.environ.copy()
    env["HOME"] = str(home_dir)

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
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert start.returncode == 0, start.stderr or start.stdout

    env_payload = _parse_env_file(root_dir / "env.sh")
    env_payload["provider_surface"] = os.environ.get("BTWIN_PROVIDER_SURFACE", "app-server")
    env_payload["provider_continuity"] = os.environ.get("BTWIN_PROVIDER_CONTINUITY", "long-term")
    env_payload["provider_model"] = os.environ.get("BTWIN_PROVIDER_MODEL", "gpt-5.4-mini")

    try:
        yield env_payload
    finally:
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
