import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app


runner = CliRunner()


def _parse_json_output(output: str):
    return json.loads(output.strip())


def _archive_path(home_dir: Path, project_root: Path) -> Path:
    canonical_path = project_root.resolve().as_posix().strip("/")
    project_key = f"path-{canonical_path.replace('/', '__').replace(':', '_')}"
    return home_dir / ".btwin" / "projects" / project_key / "handoffs.jsonl"


def _write_archive_rows(archive_path: Path, rows: list[dict]) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_handoff_write_still_updates_snapshot_and_archive(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(main, "_project_root", lambda: project_root)

    result = runner.invoke(
        app,
        [
            "handoff",
            "--record-id",
            "convo-1",
            "--summary",
            "Keep the latest snapshot behavior.",
            "--dispatch",
            "Read the saved handoff and continue from the current branch.",
        ],
    )

    assert result.exit_code == 0, result.output
    snapshot_path = project_root / "HANDOFF.md"
    assert snapshot_path.exists()
    snapshot = snapshot_path.read_text(encoding="utf-8")
    assert "# Current Handoff" in snapshot
    assert "convo-1" in snapshot

    archive_rows = [
        json.loads(line)
        for line in _archive_path(home_dir, project_root).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(archive_rows) == 1
    assert archive_rows[0]["record_id"] == "convo-1"
    assert archive_rows[0]["summary"] == "Keep the latest snapshot behavior."
    assert (project_root / ".gitignore").read_text(encoding="utf-8").splitlines() == ["HANDOFF.md"]


def test_handoff_list_reads_project_global_archive_newest_first(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(main, "_project_root", lambda: project_root)

    _write_archive_rows(
        _archive_path(home_dir, project_root),
        [
            {
                "timestamp": "2026-04-14T01:00:00+00:00",
                "record_id": "convo-1",
                "summary": "Older handoff",
                "dispatch": "old dispatch",
            },
            {
                "timestamp": "2026-04-14T02:00:00+00:00",
                "record_id": "convo-2",
                "summary": "Newer handoff",
                "dispatch": "new dispatch",
            },
        ],
    )

    result = runner.invoke(app, ["handoff", "list", "--json"])

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert [item["record_id"] for item in payload] == ["convo-2", "convo-1"]


def test_handoff_show_returns_latest_or_specific_record(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(main, "_project_root", lambda: project_root)

    _write_archive_rows(
        _archive_path(home_dir, project_root),
        [
            {
                "timestamp": "2026-04-14T01:00:00+00:00",
                "record_id": "convo-1",
                "summary": "Older handoff",
                "dispatch": "old dispatch",
            },
            {
                "timestamp": "2026-04-14T02:00:00+00:00",
                "record_id": "convo-2",
                "summary": "Latest handoff",
                "dispatch": "latest dispatch",
            },
        ],
    )

    latest_result = runner.invoke(app, ["handoff", "show", "--json"])
    assert latest_result.exit_code == 0, latest_result.output
    latest_payload = _parse_json_output(latest_result.output)
    assert latest_payload["record_id"] == "convo-2"

    specific_result = runner.invoke(app, ["handoff", "show", "convo-1", "--json"])
    assert specific_result.exit_code == 0, specific_result.output
    specific_payload = _parse_json_output(specific_result.output)
    assert specific_payload["record_id"] == "convo-1"
    assert specific_payload["summary"] == "Older handoff"


def test_handoff_show_missing_record_returns_not_found(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(main, "_project_root", lambda: project_root)

    _write_archive_rows(
        _archive_path(home_dir, project_root),
        [
            {
                "timestamp": "2026-04-14T01:00:00+00:00",
                "record_id": "convo-1",
                "summary": "Only handoff",
                "dispatch": "dispatch",
            }
        ],
    )

    result = runner.invoke(app, ["handoff", "show", "convo-missing"])

    assert result.exit_code == 4
    assert "Handoff not found" in result.output
