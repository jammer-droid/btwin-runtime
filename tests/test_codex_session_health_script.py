import os
import stat
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_stub_binaries(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kill_log = tmp_path / "kill.log"

    _write_executable(
        bin_dir / "launchctl",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "limit" && "${2:-}" == "maxfiles" ]]; then
  printf '%s\n' "${CODEX_SESSION_TEST_LAUNCHCTL_OUTPUT:-\tmaxfiles    256            unlimited      }"
  exit 0
fi
exit 1
""",
    )

    _write_executable(
        bin_dir / "pgrep",
        """#!/usr/bin/env bash
set -euo pipefail
args="$*"
if [[ "$args" == *"-P 111"* ]]; then
  printf '%s\n' "${CODEX_SESSION_TEST_CHILDREN_111:-301 btwin mcp-proxy\n302 pencil}"
  exit 0
fi
if [[ "$args" == *"-P 222"* ]]; then
  if [[ -n "${CODEX_SESSION_TEST_CHILDREN_222:-}" ]]; then
    printf '%s\n' "${CODEX_SESSION_TEST_CHILDREN_222}"
    exit 0
  fi
  exit 1
fi
if [[ "$args" == *"btwin mcp-proxy"* ]]; then
  printf '%s\n' "${CODEX_SESSION_TEST_BTWIN_MCP:-301 /tmp/btwin mcp-proxy\n303 /tmp/btwin mcp-proxy}"
  exit 0
fi
if [[ "$args" == *"visual_studio_code/out/mcp-server-darwin-arm64"* ]]; then
  printf '%s\n' "${CODEX_SESSION_TEST_PENCIL:-302 /tmp/pencil\n304 /tmp/pencil}"
  exit 0
fi
if [[ "$args" == *"codex --sandbox"* || "$args" == *"/opt/homebrew/bin/codex"* || "$args" == *"(^|/)codex"* ]]; then
  printf '%s\n' "${CODEX_SESSION_TEST_CODEX:-111 codex --sandbox danger-full-access -a never\n222 /opt/homebrew/bin/codex}"
  exit 0
fi
exit 1
""",
    )

    _write_executable(
        bin_dir / "ps",
        """#!/usr/bin/env bash
set -euo pipefail
pid=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p)
      pid="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
case "$pid" in
  111) printf '%s\n' "111 ttys011 05:20:00 codex --sandbox danger-full-access -a never" ;;
  222) printf '%s\n' "222 ttys012 00:12:00 /opt/homebrew/bin/codex" ;;
  301) printf '%s\n' "301 ttys011 05:20:00 btwin mcp-proxy" ;;
  302) printf '%s\n' "302 ttys011 05:20:00 pencil" ;;
  *) exit 1 ;;
esac
""",
    )

    _write_executable(
        bin_dir / "lsof",
        """#!/usr/bin/env bash
set -euo pipefail
pid=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p)
      pid="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
case "$pid" in
  111) count=264 ;;
  222) count=37 ;;
  *) count=15 ;;
esac
echo "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
for i in $(seq 1 "$count"); do
  echo "codex $pid home $i REG 0,0 0 0 /tmp/fd-$i"
done
""",
    )

    _write_executable(
        bin_dir / "kill",
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "{kill_log}"
""",
    )

    return {"bin_dir": str(bin_dir), "kill_log": str(kill_log)}


def _script_env(tmp_path: Path) -> dict[str, str]:
    stub_info = _make_stub_binaries(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "CODEX_SESSION_SOFT_LIMIT": "256",
            "CODEX_SESSION_PGREP_BIN": str(Path(stub_info["bin_dir"]) / "pgrep"),
            "CODEX_SESSION_PS_BIN": str(Path(stub_info["bin_dir"]) / "ps"),
            "CODEX_SESSION_LSOF_BIN": str(Path(stub_info["bin_dir"]) / "lsof"),
            "CODEX_SESSION_KILL_BIN": str(Path(stub_info["bin_dir"]) / "kill"),
            "CODEX_SESSION_LAUNCHCTL_BIN": str(Path(stub_info["bin_dir"]) / "launchctl"),
            "CODEX_SESSION_TEST_KILL_LOG": stub_info["kill_log"],
        }
    )
    return env


def test_warn_reports_risky_session_state(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "codex_session_health.sh"

    result = subprocess.run(
        ["bash", str(script_path), "warn"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=_script_env(tmp_path),
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "soft_limit=256" in result.stdout
    assert "codex_count=2" in result.stdout
    assert "btwin_mcp_count=2" in result.stdout
    assert "pencil_count=2" in result.stdout
    assert "risk=high" in result.stdout
    assert "pid=111" in result.stdout


def test_cleanup_pid_dry_run_shows_bounded_actions(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "codex_session_health.sh"
    env = _script_env(tmp_path)
    kill_log = Path(env["CODEX_SESSION_TEST_KILL_LOG"])

    result = subprocess.run(
        ["bash", str(script_path), "cleanup", "--pid", "111", "--dry-run"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "Would terminate Codex session pid=111" in result.stdout
    assert "Would terminate child pid=301" in result.stdout
    assert "Would terminate child pid=302" in result.stdout
    assert not kill_log.exists()


def test_install_local_creates_helper_entrypoints(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "codex_session_health.sh"
    bin_dir = tmp_path / "local-bin"

    result = subprocess.run(
        ["bash", str(script_path), "install-local", "--bin-dir", str(bin_dir)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=_script_env(tmp_path),
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert (bin_dir / "codex-safe-start").exists()
    assert (bin_dir / "codex-health").exists()
    assert (bin_dir / "codex-clean-stale").exists()
