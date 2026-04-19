import os
import subprocess
from pathlib import Path


def test_attached_helper_smoke_script_runs_end_to_end(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "attached_helper_smoke.sh"
    smoke_root = tmp_path / "attached-smoke"
    smoke_root.mkdir(parents=True, exist_ok=True)
    fake_bin_dir = tmp_path / "fake-bin"
    fake_bin_dir.mkdir(parents=True, exist_ok=True)
    fake_btwin_log = tmp_path / "fake-btwin.log"
    fake_btwin = fake_bin_dir / "btwin"
    fake_btwin.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "called" >> "{fake_btwin_log}"\n'
        "exit 99\n",
        encoding="utf-8",
    )
    fake_btwin.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin_dir}:{env['PATH']}"
    env["BTWIN_ATTACHED_SMOKE_ROOT"] = str(smoke_root)
    env["BTWIN_ATTACHED_SMOKE_KEEP_ROOT"] = "0"

    result = subprocess.run(
        ["bash", str(script_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "Attached helper smoke passed" in result.stdout
    assert "protocol apply-next cycle 1: review" in result.stdout
    assert "protocol apply-next cycle 2: review" in result.stdout
    assert "phase-cycle api visible: True" in result.stdout
    assert "phase-cycle next role: reviewer" in result.stdout
    assert "phase-cycle step alias: Review" in result.stdout
    assert "phase-cycle step key: review-pass" in result.stdout
    assert "phase-cycle gate key: retry-loop" in result.stdout
    assert "thread-watch retry trace: outcome=retry gate=retry-loop target=review cycle=2->3" in result.stdout
    assert "thread-watch retry policy: policy=review-outcomes emitters=reviewer,user actions=decide outcomes=retry,accept,close" in result.stdout
    assert "thread-watch blocked stop: reason=missing_contribution baseline=contribution_required" in result.stdout
    assert "thread-watch close trace: outcome=close gate=close-gate target=decision cycle=1->1" in result.stdout
    assert "thread-watch seed trace: cycle=1 procedure=review-pass target=review" in result.stdout
    assert "mailbox reports: 2" in result.stdout
    assert "hud recent activity visible: True" in result.stdout
    assert "hud phase progression visible: True" in result.stdout
    assert "hud procedure visible: True" in result.stdout
    assert "hud agent session visible: True" in result.stdout
    assert "runtime clear: False" in result.stdout
    assert "mailbox reports after clear: 2" in result.stdout
    assert "agent inbox: pending=0 diagnostics=True" in result.stdout
    assert not fake_btwin_log.exists()
    assert smoke_root.exists()
