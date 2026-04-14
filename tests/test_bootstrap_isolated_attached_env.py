import os
import shutil
import subprocess
from pathlib import Path

import yaml


def test_bootstrap_script_start_skip_server_creates_isolated_env(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "bootstrap_isolated_attached_env.sh"
    env_root = tmp_path / "isolated-env"
    project_root = tmp_path / "project-root"
    project_root.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "bash",
            str(script_path),
            "start",
            "--root",
            str(env_root),
            "--project-root",
            str(project_root),
            "--project",
            "isolated-attached-test",
            "--skip-server",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout

    config_path = env_root / "config.yaml"
    data_dir = env_root / "data"
    env_file = env_root / "env.sh"
    providers_path = data_dir / "providers.json"
    codex_config = project_root / ".codex" / "config.toml"

    assert config_path.exists()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["data_dir"] == str(data_dir)

    assert env_file.exists()
    env_text = env_file.read_text(encoding="utf-8")
    assert f'export BTWIN_CONFIG_PATH="{config_path}"' in env_text
    assert f'export BTWIN_DATA_DIR="{data_dir}"' in env_text
    assert 'export BTWIN_API_URL="http://127.0.0.1:8788"' in env_text

    assert providers_path.exists()
    assert codex_config.exists()
    codex_text = codex_config.read_text(encoding="utf-8")
    assert 'command = "btwin"' in codex_text
    assert 'args = ["mcp-proxy", "--project", "isolated-attached-test"]' in codex_text
