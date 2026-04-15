from pathlib import Path

import btwin_cli.main as main


def test_test_env_resolution_uses_repo_scoped_default_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTWIN_CONFIG_PATH", str(tmp_path / "global-config.yaml"))
    monkeypatch.setenv("BTWIN_DATA_DIR", str(tmp_path / "global-data"))
    monkeypatch.setenv("BTWIN_API_URL", "http://127.0.0.1:9999")

    assert main._test_env_root() == main._REPO_ROOT / ".btwin-test-env"
    assert main._test_env_project_root() == main._test_env_root() / "project"
    assert main._test_env_api_url() == "http://127.0.0.1:8792"
    assert main._test_env_pid_path() == main._test_env_root() / "serve-api.pid"
    assert main._test_env_log_dir() == main._test_env_root() / "logs"


def test_test_env_resolution_prefers_repo_local_btwin(tmp_path, monkeypatch):
    repo_root = tmp_path / "worktree"
    local_btwin = repo_root / ".venv" / "bin" / "btwin"
    local_btwin.parent.mkdir(parents=True)
    local_btwin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    local_btwin.chmod(0o755)

    path_btwin = tmp_path / "path-bin" / "btwin"
    path_btwin.parent.mkdir(parents=True)
    path_btwin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    path_btwin.chmod(0o755)
    monkeypatch.setenv("PATH", f"{path_btwin.parent}:{Path('/usr/bin')}")
    monkeypatch.setattr(main, "_REPO_ROOT", repo_root)

    assert main._preferred_test_env_btwin() == local_btwin


def test_test_env_resolution_falls_back_to_path_btwin(tmp_path, monkeypatch):
    repo_root = tmp_path / "worktree"
    path_btwin = tmp_path / "path-bin" / "btwin"
    path_btwin.parent.mkdir(parents=True)
    path_btwin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    path_btwin.chmod(0o755)
    monkeypatch.setenv("PATH", f"{path_btwin.parent}:{Path('/usr/bin')}")
    monkeypatch.setattr(main, "_REPO_ROOT", repo_root)

    assert main._preferred_test_env_btwin() == path_btwin
