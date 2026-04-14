from fastapi.testclient import TestClient

from btwin_cli.api_threads import create_threads_router
from btwin_core.event_bus import EventBus
from btwin_core.protocol_store import ProtocolStore
from btwin_core.thread_store import ThreadStore


def test_threads_router_exposes_agent_inbox_and_agent_status(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()

    thread = thread_store.create_thread(
        topic="Attached API thread",
        protocol="debate",
        participants=["alice", "bob"],
        initial_phase="context",
    )
    thread_store.send_message(
        thread_id=thread["thread_id"],
        from_agent="bob",
        content="Please review this.",
        tldr="review request",
        delivery_mode="direct",
        target_agents=["alice"],
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    inbox_response = client.get(f"/api/threads/{thread['thread_id']}/inbox", params={"agent": "alice"})
    assert inbox_response.status_code == 200
    assert inbox_response.json()["pending_count"] == 1

    status_response = client.get(f"/api/threads/{thread['thread_id']}/status", params={"agent": "alice"})
    assert status_response.status_code == 200
    assert status_response.json()["participant_status"] == "joined"
    assert status_response.json()["pending_message_count"] == 1
