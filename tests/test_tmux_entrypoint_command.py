import shutil
import subprocess
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app


runner = CliRunner()


def test_btwin_open_prints_fallback_when_tmux_is_missing(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_current_btwin_command_path", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "tmux" else f"/usr/bin/{name}")

    result = runner.invoke(app, ["open"])

    assert result.exit_code == 0, result.output
    assert "tmux is not installed" in result.output
    assert "codex" in result.output
    assert "btwin hud --stream" in result.output


def test_btwin_open_generates_tmux_commands_for_two_pane_layout(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_current_btwin_command_path", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    calls: list[list[str]] = []

    def fake_run(args, check):
        calls.append(list(args))
        if list(args)[:3] == ["tmux", "has-session", "-t"]:
            return subprocess.CompletedProcess(args=args, returncode=1)
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(app, ["open"])

    assert result.exit_code == 0, result.output
    assert calls[0][:3] == ["tmux", "has-session", "-t"]
    assert calls[1][:4] == ["tmux", "new-session", "-d", "-s"]
    assert any("/bin/zsh -ic" in part for part in calls[1])
    assert any("exec codex" in part for part in calls[1])
    assert calls[2][:3] == ["tmux", "split-window", "-h"]
    assert any("btwin hud --stream" in part for part in calls[2])
    assert calls[3][:3] == ["tmux", "select-layout", "-t"]
    assert calls[4][:3] == ["tmux", "attach-session", "-t"]


def test_btwin_open_no_hud_skips_split_window(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_current_btwin_command_path", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.delenv("TMUX", raising=False)

    calls: list[list[str]] = []

    def fake_run(args, check):
        calls.append(list(args))
        if list(args)[:3] == ["tmux", "has-session", "-t"]:
            return subprocess.CompletedProcess(args=args, returncode=1)
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(app, ["open", "--no-hud"])

    assert result.exit_code == 0, result.output
    assert calls[0][:3] == ["tmux", "has-session", "-t"]
    assert calls[1][:4] == ["tmux", "new-session", "-d", "-s"]
    assert all(call[:2] != ["tmux", "split-window"] for call in calls)
    assert calls[-1][:3] == ["tmux", "attach-session", "-t"]
