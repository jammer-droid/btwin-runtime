import json
import sys

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app


runner = CliRunner()


def test_init_global_creates_providers_config_and_codex_registration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda: tmp_path / ".btwin")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    providers_path = tmp_path / ".btwin" / "providers.json"
    assert providers_path.exists()
    payload = json.loads(providers_path.read_text(encoding="utf-8"))
    assert payload["providers"][0]["cli"] == "codex"
    codex_config = tmp_path / ".codex" / "config.toml"
    assert codex_config.exists()
    assert 'args = ["mcp-proxy"]' in codex_config.read_text(encoding="utf-8")
    codex_agents = tmp_path / ".codex" / "AGENTS.md"
    assert codex_agents.exists()
    codex_agents_text = codex_agents.read_text(encoding="utf-8")
    assert "B-TWIN guidance file" in codex_agents_text
    assert str(tmp_path / ".codex" / "btwin" / "AGENTS.md") in codex_agents_text
    btwin_codex_agents = tmp_path / ".codex" / "btwin" / "AGENTS.md"
    assert btwin_codex_agents.exists()
    assert "B-TWIN Session Bootstrap" in btwin_codex_agents.read_text(encoding="utf-8")
    assert "HANDOFF.md" in btwin_codex_agents.read_text(encoding="utf-8")
    assert (tmp_path / ".codex" / "skills" / "bt-handoff").exists()
    assert (tmp_path / ".btwin" / "guidelines.md").exists()
    assert (tmp_path / ".btwin" / "protocols" / "debate.yaml").exists()


def test_init_global_preserves_provider_config_while_syncing_global_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda: tmp_path / ".btwin")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    providers_path = tmp_path / ".btwin" / "providers.json"
    payload = json.loads(providers_path.read_text(encoding="utf-8"))
    assert payload["providers"][0]["cli"] == "codex"
    assert payload["providers"][0]["id"] == "openai"
    assert payload["providers"][0]["default_model"] == "gpt-5.4"


def test_init_global_preserves_existing_codex_agents_guidance(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda: tmp_path / ".btwin")
    agents_path = tmp_path / ".codex" / "AGENTS.md"
    agents_path.parent.mkdir(parents=True)
    agents_path.write_text("Existing global guidance.\n", encoding="utf-8")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    written = agents_path.read_text(encoding="utf-8")
    assert "Existing global guidance." in written
    assert "B-TWIN guidance file" in written
    assert str(tmp_path / ".codex" / "btwin" / "AGENTS.md") in written
    assert "HANDOFF.md" in (tmp_path / ".codex" / "btwin" / "AGENTS.md").read_text(
        encoding="utf-8"
    )


def test_init_global_replaces_existing_btwin_codex_agents_block(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda: tmp_path / ".btwin")
    agents_path = tmp_path / ".codex" / "AGENTS.md"
    agents_path.parent.mkdir(parents=True)
    agents_path.write_text(
        "Existing global guidance.\n\n"
        "<!-- BEGIN B-TWIN GLOBAL GUIDANCE -->\n"
        "old guidance\n"
        "<!-- END B-TWIN GLOBAL GUIDANCE -->\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    written = agents_path.read_text(encoding="utf-8")
    assert "Existing global guidance." in written
    assert "old guidance" not in written
    assert written.count("BEGIN B-TWIN GLOBAL GUIDANCE") == 1
    assert "B-TWIN guidance file" in written
    assert "B-TWIN Session Bootstrap" in (
        tmp_path / ".codex" / "btwin" / "AGENTS.md"
    ).read_text(encoding="utf-8")


def test_init_local_creates_provider_config_and_project_codex_registration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda: tmp_path / "home" / ".btwin")

    result = runner.invoke(app, ["init", "demo-project", "--local"])

    assert result.exit_code == 0, result.output
    providers_path = tmp_path / "home" / ".btwin" / "providers.json"
    assert providers_path.exists()
    codex_config = tmp_path / ".codex" / "config.toml"
    assert codex_config.exists()
    assert 'args = ["mcp-proxy", "--project", "demo-project"]' in codex_config.read_text(encoding="utf-8")


def test_init_local_writes_hooks_using_current_btwin_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda: tmp_path / "home" / ".btwin")
    monkeypatch.setattr(sys, "executable", "/tmp/current-python")

    result = runner.invoke(app, ["init", "demo-project", "--local"])

    assert result.exit_code == 0, result.output
    hooks_path = tmp_path / ".codex" / "hooks.json"
    assert hooks_path.exists()
    hooks_text = hooks_path.read_text(encoding="utf-8")
    assert '"SessionStart"' in hooks_text
    assert '"/tmp/current-python -m btwin_cli.main workflow hook"' in hooks_text


def test_init_requires_codex_cli_in_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: None)
    monkeypatch.setattr(main, "_get_active_data_dir", lambda: tmp_path / ".btwin")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "CLI not found" in result.output


def test_init_reuses_existing_provider_config_without_force(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda: tmp_path / ".btwin")
    providers_path = tmp_path / ".btwin" / "providers.json"
    providers_path.parent.mkdir(parents=True, exist_ok=True)
    providers_path.write_text('{"providers": [{"cli": "codex", "models": []}]}\n', encoding="utf-8")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert "Reusing existing provider config" in result.output
    payload = json.loads(providers_path.read_text(encoding="utf-8"))
    assert payload["providers"][0]["cli"] == "codex"


def test_init_force_overwrites_existing_provider_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(main, "_get_active_data_dir", lambda: tmp_path / ".btwin")
    providers_path = tmp_path / ".btwin" / "providers.json"
    providers_path.parent.mkdir(parents=True, exist_ok=True)
    providers_path.write_text('{"providers": []}\n', encoding="utf-8")

    result = runner.invoke(app, ["init", "--force"])

    assert result.exit_code == 0, result.output
    payload = json.loads(providers_path.read_text(encoding="utf-8"))
    assert payload["providers"][0]["cli"] == "codex"
