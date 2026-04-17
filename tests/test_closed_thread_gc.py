from pathlib import Path

import yaml

from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_store import PhaseCycleStore
from btwin_core.system_gc_log import SystemGcLog
from btwin_core.system_mailbox_store import SystemMailboxStore
from btwin_core.thread_store import ThreadStore


def _write_thread_meta(path: Path, meta: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_gc_removes_closed_lru_thread_runtime_state_and_reports(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    thread = thread_store.create_thread(
        topic="Old closed thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    closed = thread_store.close_thread(thread["thread_id"], summary="done", decision=None)
    assert closed is not None

    thread_dir = tmp_path / "threads" / thread["thread_id"]
    meta_path = thread_dir / "thread.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    meta["closed_at"] = "2026-04-10T00:00:00+00:00"
    meta["last_accessed_at"] = "2026-04-10T00:00:00+00:00"
    _write_thread_meta(meta_path, meta)

    mailbox = SystemMailboxStore(tmp_path)
    mailbox.append_report(
        {
            "thread_id": thread["thread_id"],
            "report_type": "cycle_result",
            "summary": "Old cycle result",
            "created_at": "2026-04-10T00:05:00+00:00",
        }
    )

    gc_log = SystemGcLog(tmp_path)
    result = thread_store.gc_closed_threads(
        mailbox_store=mailbox,
        gc_log=gc_log,
        max_closed_threads=0,
    )

    assert result["deleted_threads"] == 1
    assert result["deleted_reports"] == 1
    assert not thread_dir.exists()
    assert mailbox.list_reports() == []


def test_gc_writes_tombstone_without_summary(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    thread = thread_store.create_thread(
        topic="Old closed thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    closed = thread_store.close_thread(thread["thread_id"], summary="done", decision=None)
    assert closed is not None

    meta_path = tmp_path / "threads" / thread["thread_id"] / "thread.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    meta["closed_at"] = "2026-04-10T00:00:00+00:00"
    meta["last_accessed_at"] = "2026-04-10T00:00:00+00:00"
    _write_thread_meta(meta_path, meta)

    gc_log = SystemGcLog(tmp_path)
    thread_store.gc_closed_threads(
        mailbox_store=SystemMailboxStore(tmp_path),
        gc_log=gc_log,
        max_closed_threads=0,
    )

    tombstone = gc_log.list_events()[0]
    assert set(tombstone) >= {"thread_id", "deleted_at", "reason", "last_status"}
    assert "summary" not in tombstone


def test_gc_deletes_closed_thread_phase_cycle_state_but_keeps_active_threads(tmp_path):
    project_root = tmp_path / "project"
    thread_store = ThreadStore(project_root / "threads")
    phase_cycle_store = PhaseCycleStore(project_root)
    mailbox = SystemMailboxStore(project_root)

    active_thread = thread_store.create_thread(
        topic="Active thread",
        protocol="review-loop",
        participants=["alice"],
        initial_phase="review",
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=active_thread["thread_id"],
            phase_name="review",
            procedure_steps=["review", "revise"],
        )
    )

    closed_thread = thread_store.create_thread(
        topic="Closed thread",
        protocol="review-loop",
        participants=["alice"],
        initial_phase="review",
    )
    thread_store.close_thread(closed_thread["thread_id"], summary="done", decision=None)
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=closed_thread["thread_id"],
            phase_name="review",
            procedure_steps=["review", "revise"],
        )
    )

    mailbox.append_report(
        {
            "thread_id": active_thread["thread_id"],
            "report_type": "cycle_result",
            "summary": "Active thread report",
            "created_at": "2026-04-17T00:00:00+00:00",
        }
    )
    mailbox.append_report(
        {
            "thread_id": closed_thread["thread_id"],
            "report_type": "cycle_result",
            "summary": "Closed thread report",
            "created_at": "2026-04-17T00:01:00+00:00",
        }
    )

    result = thread_store.gc_closed_threads(
        mailbox_store=mailbox,
        gc_log=SystemGcLog(project_root),
        max_closed_threads=0,
    )

    assert result["deleted_threads"] == 1
    assert result["deleted_reports"] == 1
    assert phase_cycle_store.read(closed_thread["thread_id"]) is None
    assert phase_cycle_store.read(active_thread["thread_id"]) is not None
    assert (project_root / "threads" / active_thread["thread_id"]).exists()
    assert not (project_root / "threads" / closed_thread["thread_id"]).exists()
    assert [report["thread_id"] for report in mailbox.list_reports()] == [active_thread["thread_id"]]
