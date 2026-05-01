from pathlib import Path

from typer.testing import CliRunner

from btwin_cli.main import app


runner = CliRunner()


def test_install_skills_codex_reports_init_as_preferred_global_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["install-skills", "--platform", "codex"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".agents" / "skills" / "bt-handoff").exists()
    assert "btwin init" in result.output
    assert "preferred" in result.output.lower()


def test_install_skills_codex_copies_when_symlinks_are_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    def raise_symlink_error(self, target, target_is_directory=False):
        raise OSError("symlinks unavailable")

    monkeypatch.setattr(Path, "symlink_to", raise_symlink_error)

    result = runner.invoke(app, ["install-skills", "--platform", "codex"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".agents" / "skills" / "bt-handoff" / "SKILL.md").exists()
