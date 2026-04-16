import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_runner(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    script_path = _repo_root() / "scripts" / "run_tests.py"
    command = [
        sys.executable,
        str(script_path),
        *args,
        "--artifact-root",
        str(tmp_path),
        "--pytest-arg",
        "tests/test_provider_init.py::test_codex_is_the_only_available_provider",
    ]
    return subprocess.run(
        command,
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_pytest_markers_are_registered() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--markers"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "provider_smoke" in result.stdout
    assert "cli_smoke" in result.stdout
    assert "integration" in result.stdout
    assert "unit" in result.stdout


def test_run_tests_creates_run_directory_and_latest_link(tmp_path: Path) -> None:
    result = _run_runner(tmp_path, "unit")

    assert result.returncode == 0, result.stderr or result.stdout
    latest = tmp_path / "latest"
    assert latest.exists()
    assert latest.is_symlink()
    run_dir = latest.resolve()
    assert run_dir.exists()
    assert (run_dir / "report.html").exists()
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["group"] == "unit"


def test_run_tests_prunes_old_runs_and_keeps_configured_count(tmp_path: Path) -> None:
    for index in range(35):
        run_dir = tmp_path / f"2026-04-16T00-00-{index:02d}-unit"
        run_dir.mkdir(parents=True)
        (run_dir / "metadata.json").write_text("{}", encoding="utf-8")

    result = _run_runner(tmp_path, "unit", "--keep-runs", "30")

    assert result.returncode == 0, result.stderr or result.stdout
    kept_runs = sorted(path for path in tmp_path.iterdir() if path.name != "latest")
    assert len(kept_runs) == 30


def test_provider_smoke_skips_when_provider_preflight_is_unavailable(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = ""

    script_path = _repo_root() / "scripts" / "run_tests.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "provider-smoke",
            "--artifact-root",
            str(tmp_path),
        ],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    latest = tmp_path / "latest"
    assert latest.exists()
    run_dir = latest.resolve()
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "skipped"
    assert metadata["skip_reason"]


def test_run_tests_all_group_excludes_provider_by_default(tmp_path: Path) -> None:
    result = _run_runner(tmp_path, "all")

    assert result.returncode == 0, result.stderr or result.stdout
    latest = tmp_path / "latest"
    metadata = json.loads((latest.resolve() / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["group"] == "all"
    assert metadata["provider_smoke_included"] is False


def test_cli_smoke_group_runs_cli_smoke_target(tmp_path: Path) -> None:
    result = _run_runner(
        tmp_path,
        "cli-smoke",
        "--pytest-arg",
        "tests/test_attached_helper_smoke_script.py::test_attached_helper_smoke_script_runs_end_to_end",
    )

    assert result.returncode == 0, result.stderr or result.stdout
    metadata = json.loads(((tmp_path / "latest").resolve() / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["group"] == "cli-smoke"


def test_integration_group_runs_integration_target(tmp_path: Path) -> None:
    result = _run_runner(
        tmp_path,
        "integration",
        "--pytest-arg",
        "tests/test_bootstrap_isolated_attached_env.py::test_bootstrap_script_start_skip_server_creates_isolated_env",
    )

    assert result.returncode == 0, result.stderr or result.stdout
    metadata = json.loads(((tmp_path / "latest").resolve() / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["group"] == "integration"
