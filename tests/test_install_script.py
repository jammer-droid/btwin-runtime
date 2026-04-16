from __future__ import annotations

import subprocess
from pathlib import Path


def test_macos_install_script_dry_run_lists_expected_steps() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "install_btwin_macos.sh"

    result = subprocess.run(
        ["bash", str(script_path), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo_root,
    )

    assert result.returncode == 0
    assert "uv sync" in result.stdout
    assert "uv tool install -e" in result.stdout
    assert "btwin init" in result.stdout
    assert "service install" in result.stdout
    assert "BTWIN_DATA_DIR=/Users/home/.btwin" in result.stdout
    assert "BTWIN_CONFIG_PATH=/Users/home/.btwin/config.yaml" in result.stdout
