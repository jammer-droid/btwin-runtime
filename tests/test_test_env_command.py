import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app


runner = CliRunner()


def test_test_env_up_prepares_workspace_and_status(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo_agents = repo_root / "AGENTS.md"
    repo_agents.write_text("repo instructions\n", encoding="utf-8")

    local_btwin = repo_root / ".venv" / "bin" / "btwin"
    local_btwin.parent.mkdir(parents=True, exist_ok=True)
    local_btwin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    local_btwin.chmod(0o755)

    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(main, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(main, "_test_env_project_name", lambda: "repo-test-env")
    monkeypatch.setattr(main, "validate_provider_cli", lambda provider_name: "/usr/bin/codex")

    healthy_calls: list[bool] = [False]

    def fake_healthcheck(_api_url: str) -> bool:
        if healthy_calls:
            return healthy_calls.pop(0)
        return True

    monkeypatch.setattr(main, "_test_env_api_is_healthy", fake_healthcheck, raising=False)

    start_calls: list[tuple[object, int, str]] = []

    def fake_start_process(btwin_bin, port, api_url):
        start_calls.append((btwin_bin, port, api_url))
        main._test_env_pid_path().write_text("4242\n", encoding="utf-8")
        main._test_env_owner_path().write_text(f"{main._test_env_owner_id()}\n", encoding="utf-8")
        return 4242

    monkeypatch.setattr(main, "_start_test_env_process", fake_start_process)

    result = runner.invoke(app, ["test-env", "up"])

    assert result.exit_code == 0, result.output

    root = repo_root / ".btwin-test-env"
    project_root = root / "project"
    config_path = root / "config.yaml"
    providers_path = root / "data" / "providers.json"
    codex_config = project_root / ".codex" / "config.toml"
    project_agents = project_root / "AGENTS.md"
    pid_path = root / "serve-api.pid"

    assert config_path.exists()
    assert providers_path.exists()
    assert codex_config.exists()
    assert project_agents.exists()
    assert pid_path.read_text(encoding="utf-8") == "4242\n"

    config_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config_payload["data_dir"] == str(root / "data")

    providers_payload = json.loads(providers_path.read_text(encoding="utf-8"))
    assert providers_payload["providers"][0]["cli"] == "codex"

    codex_text = codex_config.read_text(encoding="utf-8")
    assert 'command = "btwin"' in codex_text
    assert 'args = ["mcp-proxy", "--project", "repo-test-env"]' in codex_text

    agents_text = project_agents.read_text(encoding="utf-8")
    assert "isolated test environment" in agents_text.lower()
    assert "btwin test-env status" in agents_text
    assert repo_agents.read_text(encoding="utf-8") == "repo instructions\n"

    assert start_calls == [(local_btwin, 8792, "http://127.0.0.1:8792")]

    status_result = runner.invoke(app, ["test-env", "status"])

    assert status_result.exit_code == 0, status_result.output
    assert f"Root: {root}" in status_result.output
    assert f"Project root: {project_root}" in status_result.output
    assert "API: http://127.0.0.1:8792" in status_result.output
    assert "API health: ok" in status_result.output


def test_test_env_start_records_process_identity(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(main, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(main, "_test_env_project_name", lambda: "repo-test-env")
    monkeypatch.setattr(main, "_test_env_api_is_healthy", lambda api_url: True, raising=False)
    monkeypatch.setattr(main, "_test_env_nonce", lambda: "nonce-abc")
    main._test_env_root().mkdir(parents=True, exist_ok=True)
    main._test_env_data_dir().mkdir(parents=True, exist_ok=True)
    main._test_env_log_dir().mkdir(parents=True, exist_ok=True)
    main._test_env_project_root().mkdir(parents=True, exist_ok=True)

    class FakeProcess:
        pid = 4242

        def terminate(self):
            raise AssertionError("terminate should not be called")

    popen_argv: list[str] = []

    def fake_popen(*args, **kwargs):
        popen_argv[:] = list(args[0])
        return FakeProcess()

    def fake_ps_run(args, capture_output, text, check):
        if args == ["ps", "-p", "4242", "-o", "lstart="]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="Mon Apr 15 12:34:56 2026\n",
                stderr="",
            )
        raise AssertionError(f"unexpected ps call: {args}")

    monkeypatch.setattr(main.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(main.subprocess, "run", fake_ps_run)

    pid = main._start_test_env_process(Path("/opt/btwin/bin/btwin"), 8792, "http://127.0.0.1:8792")

    assert pid == 4242
    assert popen_argv[0] == main.sys.executable
    assert popen_argv[1] == str(main._test_env_wrapper_path())
    assert popen_argv[2] == "--nonce=nonce-abc"
    assert popen_argv[3] == "/opt/btwin/bin/btwin"
    assert popen_argv[4] == "8792"
    assert main._test_env_wrapper_path().exists()
    wrapper_text = main._test_env_wrapper_path().read_text(encoding="utf-8")
    assert "child.terminate()" in wrapper_text
    assert "signal.signal(signal.SIGTERM" in wrapper_text
    identity_path = main._test_env_identity_path()
    assert identity_path.exists()
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    assert identity == {"pid": 4242, "start_time": "Mon Apr 15 12:34:56 2026", "nonce": "nonce-abc"}


def test_test_env_hud_scopes_global_hud_to_test_env(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(main, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(main, "_test_env_project_name", lambda: "repo-test-env")

    def fake_ensure_test_env_up(port: int = 8792):
        root = main._test_env_root()
        root.mkdir(parents=True, exist_ok=True)
        main._test_env_data_dir().mkdir(parents=True, exist_ok=True)
        main._test_env_project_root().mkdir(parents=True, exist_ok=True)
        main._test_env_config_path().write_text(
            yaml.safe_dump({"data_dir": str(main._test_env_data_dir())}, sort_keys=False),
            encoding="utf-8",
        )
        return 4242, True

    monkeypatch.setattr(main, "_ensure_test_env_up", fake_ensure_test_env_up)

    captured: dict[str, object] = {}

    def fake_hud(
        *,
        thread_id: str | None = None,
        threads: bool = False,
        limit: int = 10,
        follow: bool = False,
        stream: bool = False,
        interval: float = 1.0,
    ) -> None:
        captured["cwd"] = Path.cwd()
        captured["config_path"] = main._config_path()
        captured["data_dir"] = main._btwin_data_dir()
        captured["api_url"] = os.environ.get("BTWIN_API_URL")
        captured["thread_id"] = thread_id
        captured["threads"] = threads
        captured["limit"] = limit
        captured["follow"] = follow
        captured["stream"] = stream
        captured["interval"] = interval

    monkeypatch.setattr(main, "hud", fake_hud)

    result = runner.invoke(app, ["test-env", "hud", "--thread", "thread-1", "--limit", "3"])

    assert result.exit_code == 0, result.output
    assert captured["cwd"] == main._test_env_project_root()
    assert captured["config_path"] == main._test_env_config_path()
    assert captured["data_dir"] == main._test_env_data_dir()
    assert captured["api_url"] == main._test_env_api_url()
    assert captured["thread_id"] == "thread-1"
    assert captured["threads"] is False
    assert captured["limit"] == 3
    assert captured["follow"] is False
    assert captured["stream"] is False
    assert captured["interval"] == 1.0


def test_test_env_down_stops_only_owned_process(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(main, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(main, "_test_env_nonce", lambda: "nonce-abc")

    root = main._test_env_root()
    root.mkdir(parents=True, exist_ok=True)
    pid_path = main._test_env_pid_path()
    owner_path = main._test_env_owner_path()
    identity_path = main._test_env_identity_path()
    pid_path.write_text("4242\n", encoding="utf-8")
    owner_path.write_text(f"{main._test_env_owner_id()}\n", encoding="utf-8")
    identity_path.write_text(
        json.dumps({"pid": 4242, "start_time": "Mon Apr 15 12:34:56 2026", "nonce": "nonce-abc"}, indent=2) + "\n",
        encoding="utf-8",
    )

    killed: list[tuple[int, object]] = []

    def fake_ps_run(args, capture_output, text, check):
        if args == ["ps", "-p", "4242", "-o", "lstart="]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="Mon Apr 15 12:34:56 2026\n",
                stderr="",
            )
        if args == ["ps", "-p", "4242", "-o", "command="]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=f"/usr/bin/python {main._test_env_wrapper_path()} --nonce=nonce-abc /opt/btwin/bin/btwin 8792\n",
                stderr="",
            )
        raise AssertionError(f"unexpected ps call: {args}")

    monkeypatch.setattr(main.subprocess, "run", fake_ps_run)
    monkeypatch.setattr(main.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(main, "_test_env_pid_is_running", lambda pid: True)

    result = runner.invoke(app, ["test-env", "down"])

    assert result.exit_code == 0, result.output
    assert killed == [(4242, main.signal.SIGTERM)]
    assert not pid_path.exists()
    assert not owner_path.exists()
    assert not identity_path.exists()


@pytest.mark.parametrize("pid_text", ["0\n", "-1\n"])
def test_test_env_down_rejects_non_positive_pid(tmp_path, monkeypatch, pid_text):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(main, "_REPO_ROOT", repo_root)

    root = main._test_env_root()
    root.mkdir(parents=True, exist_ok=True)
    pid_path = main._test_env_pid_path()
    owner_path = main._test_env_owner_path()
    identity_path = main._test_env_identity_path()
    pid_path.write_text(pid_text, encoding="utf-8")
    owner_path.write_text(f"{main._test_env_owner_id()}\n", encoding="utf-8")
    identity_path.write_text(
        json.dumps({"pid": 1, "start_time": "Mon Apr 15 12:34:56 2026"}, indent=2) + "\n",
        encoding="utf-8",
    )

    killed: list[tuple[int, object]] = []

    def fake_ps_run(*args, **kwargs):
        raise AssertionError("ps should not be queried for non-positive pids")

    monkeypatch.setattr(main.subprocess, "run", fake_ps_run)
    monkeypatch.setattr(main.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    result = runner.invoke(app, ["test-env", "down"])

    assert result.exit_code == 0, result.output
    assert killed == []
    assert not pid_path.exists()
    assert not owner_path.exists()
    assert not identity_path.exists()


def test_test_env_down_skips_unowned_process(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(main, "_REPO_ROOT", repo_root)

    root = main._test_env_root()
    root.mkdir(parents=True, exist_ok=True)
    pid_path = main._test_env_pid_path()
    owner_path = main._test_env_owner_path()
    identity_path = main._test_env_identity_path()
    pid_path.write_text("7777\n", encoding="utf-8")
    owner_path.write_text("someone-else\n", encoding="utf-8")
    identity_path.write_text(
        json.dumps({"pid": 7777, "start_time": "Mon Apr 15 12:34:56 2026"}, indent=2) + "\n",
        encoding="utf-8",
    )

    killed: list[tuple[int, object]] = []

    monkeypatch.setattr(main.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(main, "_test_env_pid_is_running", lambda pid: True)

    result = runner.invoke(app, ["test-env", "down"])

    assert result.exit_code == 0, result.output
    assert killed == []
    assert not pid_path.exists()
    assert not owner_path.exists()
    assert not identity_path.exists()


def test_test_env_down_refuses_nonce_mismatch(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(main, "_REPO_ROOT", repo_root)

    root = main._test_env_root()
    root.mkdir(parents=True, exist_ok=True)
    pid_path = main._test_env_pid_path()
    owner_path = main._test_env_owner_path()
    identity_path = main._test_env_identity_path()
    pid_path.write_text("4242\n", encoding="utf-8")
    owner_path.write_text(f"{main._test_env_owner_id()}\n", encoding="utf-8")
    identity_path.write_text(
        json.dumps({"pid": 4242, "start_time": "Mon Apr 15 12:34:56 2026", "nonce": "nonce-recorded"}, indent=2)
        + "\n",
        encoding="utf-8",
    )

    killed: list[tuple[int, object]] = []

    def fake_ps_run(args, capture_output, text, check):
        if args == ["ps", "-p", "4242", "-o", "lstart="]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="Mon Apr 15 12:34:56 2026\n",
                stderr="",
            )
        if args == ["ps", "-p", "4242", "-o", "command="]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=f"/usr/bin/python {main._test_env_wrapper_path()} --nonce=nonce-other /opt/btwin/bin/btwin 8792\n",
                stderr="",
            )
        raise AssertionError(f"unexpected ps call: {args}")

    monkeypatch.setattr(main.subprocess, "run", fake_ps_run)
    monkeypatch.setattr(main.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(main, "_test_env_pid_is_running", lambda pid: True)

    result = runner.invoke(app, ["test-env", "down"])

    assert result.exit_code == 0, result.output
    assert killed == []
    assert not pid_path.exists()
    assert not owner_path.exists()
    assert not identity_path.exists()
