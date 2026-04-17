from pathlib import Path

from btwin_core.system_mailbox_store import SystemMailboxStore
from btwin_core.thread_store import ThreadStore


def test_system_mailbox_store_lists_cycle_reports_newest_first(tmp_path):
    store = SystemMailboxStore(tmp_path)
    store.append_report(
        {
            "thread_id": "thread-1",
            "report_type": "cycle_result",
            "summary": "First report",
            "created_at": "2026-04-17T00:00:00+00:00",
        }
    )
    store.append_report(
        {
            "thread_id": "thread-1",
            "report_type": "cycle_result",
            "summary": "Second report",
            "created_at": "2026-04-17T00:01:00+00:00",
        }
    )

    payload = store.list_reports()

    assert [item["summary"] for item in payload] == ["Second report", "First report"]
    assert payload[0]["report_type"] == "cycle_result"
    assert [item["summary"] for item in store.list_reports(thread_id="thread-1", limit=1)] == ["Second report"]


def test_system_mailbox_store_keeps_newest_first_per_thread(tmp_path):
    store = SystemMailboxStore(tmp_path)
    store.append_report(
        {
            "thread_id": "thread-1",
            "report_type": "cycle_result",
            "summary": "Thread 1 older",
            "created_at": "2026-04-17T00:00:00+00:00",
        }
    )
    store.append_report(
        {
            "thread_id": "thread-2",
            "report_type": "cycle_result",
            "summary": "Thread 2 newer",
            "created_at": "2026-04-17T00:02:00+00:00",
        }
    )
    store.append_report(
        {
            "thread_id": "thread-1",
            "report_type": "cycle_result",
            "summary": "Thread 1 newer",
            "created_at": "2026-04-17T00:01:00+00:00",
        }
    )

    payload = store.list_reports(thread_id="thread-1")

    assert [item["summary"] for item in payload] == ["Thread 1 newer", "Thread 1 older"]


def test_system_mailbox_is_not_mixed_with_agent_inbox(tmp_path):
    project_root = tmp_path / "project"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    thread = thread_store.create_thread(
        topic="Mailbox separation",
        protocol="debate",
        participants=["alice", "bob"],
        initial_phase="context",
    )
    thread_store.send_message(
        thread_id=thread["thread_id"],
        from_agent="bob",
        content="Please review.",
        tldr="review request",
        delivery_mode="direct",
        target_agents=["alice"],
    )

    mailbox = SystemMailboxStore(project_root / ".btwin")
    mailbox.append_report(
        {
            "thread_id": thread["thread_id"],
            "report_type": "cycle_result",
            "summary": "Cycle complete",
            "created_at": "2026-04-17T00:00:00+00:00",
        }
    )

    assert thread_store.list_inbox(thread["thread_id"], "alice") != mailbox.list_reports()
    assert len(mailbox.list_reports()) == 1
