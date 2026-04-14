import plistlib
import subprocess

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig


runner = CliRunner()


def test_service_install_creates_launchagent_files_and_bootstraps(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
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

    plist_path = tmp_path / ".btwin" / "com.btwin.serve-api.plist"
    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["Label"] == "com.btwin.serve-api"
    assert payload["ProgramArguments"] == [str(btwin_bin), "serve-api"]
    assert payload["StandardOutPath"] == str(tmp_path / ".btwin" / "logs" / "serve-api.stdout.log")
    assert payload["StandardErrorPath"] == str(tmp_path / ".btwin" / "logs" / "serve-api.stderr.log")

    link_path = tmp_path / "Library" / "LaunchAgents" / "com.btwin.serve-api.plist"
    assert link_path.is_symlink()
    assert link_path.resolve() == plist_path.resolve()
    assert (tmp_path / ".btwin" / "logs").is_dir()

    assert calls == [
        ["launchctl", "bootout", "gui/501/com.btwin.serve-api"],
        ["launchctl", "bootstrap", "gui/501", str(link_path)],
    ]


def test_service_install_uses_global_btwin_dir_even_when_active_config_is_local(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(main.os, "getuid", lambda: 501)

    local_data_dir = tmp_path / "project" / ".btwin"
    monkeypatch.setattr(
        main,
        "_get_config",
        lambda: BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=local_data_dir),
    )

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

    global_plist = tmp_path / ".btwin" / "com.btwin.serve-api.plist"
    local_plist = local_data_dir / "com.btwin.serve-api.plist"
    assert global_plist.exists()
    assert not local_plist.exists()

    payload = plistlib.loads(global_plist.read_bytes())
    assert payload["StandardOutPath"] == str(tmp_path / ".btwin" / "logs" / "serve-api.stdout.log")
    assert payload["StandardErrorPath"] == str(tmp_path / ".btwin" / "logs" / "serve-api.stderr.log")

    link_path = tmp_path / "Library" / "LaunchAgents" / "com.btwin.serve-api.plist"
    assert link_path.resolve() == global_plist.resolve()
    assert calls == [
        ["launchctl", "bootout", "gui/501/com.btwin.serve-api"],
        ["launchctl", "bootstrap", "gui/501", str(link_path)],
    ]


def test_service_install_requires_btwin_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(main.shutil, "which", lambda name: None)

    result = runner.invoke(app, ["service", "install"])

    assert result.exit_code == 1
    assert "Could not find `btwin` executable" in result.output


def test_service_status_prints_launchctl_output(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(main.os, "getuid", lambda: 501)

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="service state\n", stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = runner.invoke(app, ["service", "status"])

    assert result.exit_code == 0, result.output
    assert "service state" in result.output
    assert calls == [["launchctl", "print", "gui/501/com.btwin.serve-api"]]


def test_service_restart_runs_kickstart(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(main.os, "getuid", lambda: 501)

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = runner.invoke(app, ["service", "restart"])

    assert result.exit_code == 0, result.output
    assert calls == [["launchctl", "kickstart", "-k", "gui/501/com.btwin.serve-api"]]


def test_service_stop_runs_bootout(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(main.os, "getuid", lambda: 501)

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = runner.invoke(app, ["service", "stop"])

    assert result.exit_code == 0, result.output
    assert calls == [["launchctl", "bootout", "gui/501/com.btwin.serve-api"]]
