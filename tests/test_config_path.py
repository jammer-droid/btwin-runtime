import subprocess

import yaml
from typer.testing import CliRunner

import btwin_cli.api_app as api_app
import btwin_cli.main as main
from btwin_cli.main import app


runner = CliRunner()


def _write_config(path, *, data_dir):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"data_dir": str(data_dir)}), encoding="utf-8")


def test_setup_uses_btwin_config_path_and_btwin_data_dir_env(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    config_path = tmp_path / "configs" / "test-config.yaml"
    data_dir = tmp_path / "runtime-data"

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("BTWIN_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("BTWIN_DATA_DIR", str(data_dir))

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0, result.output
    assert config_path.exists()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["data_dir"] == str(data_dir)
    assert not (home_dir / ".btwin" / "config.yaml").exists()


def test_init_uses_custom_config_path_to_find_active_data_dir(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    config_path = tmp_path / "configs" / "test-config.yaml"
    data_dir = tmp_path / "runtime-data"
    _write_config(config_path, data_dir=data_dir)

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("BTWIN_CONFIG_PATH", str(config_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("btwin_cli.provider_init.shutil.which", lambda name: f"/usr/bin/{name}")

    result = runner.invoke(app, ["init", "demo-project", "--local"])

    assert result.exit_code == 0, result.output
    assert (data_dir / "providers.json").exists()
    assert not (home_dir / ".btwin" / "providers.json").exists()
    codex_config = tmp_path / ".codex" / "config.toml"
    assert codex_config.exists()


def test_service_install_uses_active_data_dir_from_custom_config_path(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    config_path = tmp_path / "configs" / "test-config.yaml"
    data_dir = tmp_path / "runtime-data"
    _write_config(config_path, data_dir=data_dir)

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("BTWIN_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(main.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(main.os, "getuid", lambda: 501)

    btwin_bin = tmp_path / "bin" / "btwin"
    btwin_bin.parent.mkdir(parents=True, exist_ok=True)
    btwin_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(main.shutil, "which", lambda name: str(btwin_bin))

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = runner.invoke(app, ["service", "install"])

    assert result.exit_code == 0, result.output
    assert (data_dir / "com.btwin.serve-api.plist").exists()
    assert (data_dir / "logs").is_dir()
    assert not (home_dir / ".btwin" / "com.btwin.serve-api.plist").exists()
    assert calls == [
        ["launchctl", "bootout", "gui/501/com.btwin.serve-api"],
        ["launchctl", "bootstrap", "gui/501", str(home_dir / "Library" / "LaunchAgents" / "com.btwin.serve-api.plist")],
    ]


def test_create_default_app_uses_btwin_config_path(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    config_path = tmp_path / "configs" / "test-config.yaml"
    data_dir = tmp_path / "runtime-data"
    _write_config(config_path, data_dir=data_dir)

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("BTWIN_CONFIG_PATH", str(config_path))

    captured = {}

    def fake_create_app(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(api_app, "create_app", fake_create_app)

    api_app.create_default_app()

    assert captured["data_dir"] == data_dir
    assert captured["config"].data_dir == data_dir
