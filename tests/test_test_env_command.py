import json

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
