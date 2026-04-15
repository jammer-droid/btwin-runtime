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
    repo_venv_bin = repo_root / ".venv" / "bin"
    assert f'export BTWIN_CONFIG_PATH="{config_path}"' in env_text
    assert f'export BTWIN_DATA_DIR="{data_dir}"' in env_text
    assert 'export BTWIN_API_URL="http://127.0.0.1:8788"' in env_text
    assert f'if [[ -d "{repo_venv_bin}" ]]; then' in env_text
    assert f'export PATH="{repo_venv_bin}:$PATH"' in env_text
    assert "btwin_test_up()" in env_text
    assert "btwin_test_hud()" in env_text
    assert "btwin_test_status()" in env_text
    assert "btwin_test_down()" in env_text

    smoke = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            'source "$1"; declare -F btwin_test_up; declare -F btwin_test_hud; declare -F btwin_test_status; declare -F btwin_test_down',
            "_",
            str(env_file),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env={"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert smoke.returncode == 0, smoke.stderr or smoke.stdout
    smoke_lines = [line for line in smoke.stdout.splitlines() if line.strip()]
    assert smoke_lines == [
        "btwin_test_up",
        "btwin_test_hud",
        "btwin_test_status",
        "btwin_test_down",
    ]

    assert providers_path.exists()
    assert codex_config.exists()
    codex_text = codex_config.read_text(encoding="utf-8")
    assert 'command = "btwin"' in codex_text
    assert 'args = ["mcp-proxy", "--project", "isolated-attached-test"]' in codex_text


def test_bootstrap_env_helpers_use_captured_binary_and_owned_pid(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "bootstrap_isolated_attached_env.sh"
    env_root = tmp_path / "isolated-env"
    project_root = tmp_path / "project-root"
    project_root.mkdir(parents=True, exist_ok=True)

    captured_bin_dir = tmp_path / "captured-bin"
    captured_bin_dir.mkdir(parents=True, exist_ok=True)
    captured_log = tmp_path / "captured.log"
    captured_marker = tmp_path / "captured-marker"
    captured_hud_log = tmp_path / "captured-hud.log"
    captured_btwin = captured_bin_dir / "btwin"
    captured_btwin.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "%s\\n" "$0 $*" >> "${BTWIN_CAPTURE_LOG}"',
                'if [[ "${1:-}" == "serve-api" ]]; then',
                '  touch "${BTWIN_CAPTURE_MARKER}"',
                '  sleep 60',
                '  exit 0',
                'fi',
                'if [[ "${1:-}" == "hud" ]]; then',
                '  printf "%s\\n" "$*" >> "${BTWIN_CAPTURE_HUD_LOG}"',
                '  exit 0',
                'fi',
                'exit 0',
                "",
            ]
        ),
        encoding="utf-8",
    )
    captured_btwin.chmod(0o755)

    path_btwin_dir = tmp_path / "path-bin"
    path_btwin_dir.mkdir(parents=True, exist_ok=True)
    path_btwin = path_btwin_dir / "btwin"
    path_btwin.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "PATH btwin should not have been used\\n" >&2',
                "exit 91",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path_btwin.chmod(0o755)

    curl_dir = tmp_path / "curl-bin"
    curl_dir.mkdir(parents=True, exist_ok=True)
    curl_stub = curl_dir / "curl"
    curl_stub.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ -f "${BTWIN_CAPTURE_MARKER}" ]]; then',
                "  exit 0",
                "fi",
                "exit 7",
                "",
            ]
        ),
        encoding="utf-8",
    )
    curl_stub.chmod(0o755)

    bootstrap_env = os.environ.copy()
    bootstrap_env.update(
        {
            "PATH": f"{captured_bin_dir}:{bootstrap_env['PATH']}",
            "BTWIN_CAPTURE_LOG": str(captured_log),
            "BTWIN_CAPTURE_MARKER": str(captured_marker),
            "BTWIN_CAPTURE_HUD_LOG": str(captured_hud_log),
        }
    )

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
        env=bootstrap_env,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout

    env_file = env_root / "env.sh"
    assert env_file.exists()
    env_text = env_file.read_text(encoding="utf-8")
    assert f'export BTWIN_TEST_BTWIN_BIN="{captured_btwin}"' in env_text
    assert "BTWIN_TEST_OWNER_ID" in env_text
    assert "BTWIN_TEST_OWNER_FILE" in env_text
    assert "BTWIN_TEST_LOG_DIR" in env_text

    smoke_env = os.environ.copy()
    smoke_env.update(
        {
            "PATH": f"{curl_dir}:{path_btwin_dir}:{smoke_env['PATH']}",
            "BTWIN_CAPTURE_LOG": str(captured_log),
            "BTWIN_CAPTURE_MARKER": str(captured_marker),
            "BTWIN_CAPTURE_HUD_LOG": str(captured_hud_log),
        }
    )

    smoke = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            "\n".join(
                [
                    "set -euo pipefail",
                    'source "$1"',
                    'PATH="$2:$PATH"',
                    "sleep 60 &",
                    "bogus_pid=$!",
                    'trap \'kill "$bogus_pid" >/dev/null 2>&1 || true\' EXIT',
                    'printf "%s\\n" "$bogus_pid" > "$BTWIN_TEST_PID_PATH"',
                    "btwin_test_status",
                    "btwin_test_up",
                    'owned_pid="$(cat "$BTWIN_TEST_PID_PATH")"',
                    "btwin_test_hud --thread demo",
                    "btwin_test_down",
                    'if kill -0 "$bogus_pid" >/dev/null 2>&1; then',
                    "  :",
                    "else",
                    '  echo "bogus pid was killed" >&2',
                    "  exit 12",
                    "fi",
                    'if kill -0 "$owned_pid" >/dev/null 2>&1; then',
                    '  echo "owned pid is still alive after down" >&2',
                    "  exit 13",
                    "fi",
                    'kill "$bogus_pid" >/dev/null 2>&1 || true',
                ]
            ),
            "_",
            str(env_file),
            str(path_btwin_dir),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=smoke_env,
        check=False,
    )

    assert smoke.returncode == 0, smoke.stderr or smoke.stdout
    assert captured_log.read_text(encoding="utf-8").splitlines()[0].startswith(
        f"{captured_btwin} setup"
    )
    captured_invocations = captured_log.read_text(encoding="utf-8")
    assert f"{captured_btwin} serve-api --port 8788" in captured_invocations
    assert f"{captured_btwin} hud --thread demo" in captured_invocations
    assert path_btwin.exists()
    assert "PATH btwin should not have been used" not in smoke.stderr
    assert captured_hud_log.read_text(encoding="utf-8").strip() == "hud --thread demo"
