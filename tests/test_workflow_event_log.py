import importlib

from btwin_core.thread_store import ThreadStore


def test_thread_store_exposes_workflow_event_log_path(tmp_path):
    store = ThreadStore(tmp_path / "project" / ".btwin" / "threads")
    thread = store.create_thread(
        topic="Workflow HUD",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )

    log_path = store.workflow_event_log_path(thread["thread_id"])

    assert log_path.name == "workflow-events.jsonl"
    assert log_path.parent == tmp_path / "project" / ".btwin" / "threads" / thread["thread_id"]


def test_append_and_list_thread_workflow_events(tmp_path):
    module = importlib.import_module("btwin_core.workflow_event_log")
    WorkflowEventLog = module.WorkflowEventLog

    log = WorkflowEventLog(tmp_path / "workflow-events.jsonl")
    log.append(
        {
            "timestamp": "2026-04-15T01:09:10+00:00",
            "thread_id": "thread-20260415-demo",
            "agent": "alice",
            "phase": "context",
            "event_type": "hook_decision",
            "hook_event_name": "Stop",
            "decision": "block",
            "reason": "missing_contribution",
            "summary": "Stop blocked until alice contributes.",
        }
    )

    events = log.list_events()

    assert len(events) == 1
    assert events[0]["event_type"] == "hook_decision"
    assert events[0]["decision"] == "block"
    assert events[0]["summary"] == "Stop blocked until alice contributes."
