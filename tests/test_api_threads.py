from pathlib import Path

from fastapi.testclient import TestClient

from btwin_cli.api_threads import create_threads_router
from btwin_core.event_bus import EventBus
from btwin_core.protocol_store import ProtocolStore
from btwin_core.thread_store import ThreadStore


class _FakeAgentRunner:
    def __init__(self, active_threads_by_agent):
        self._active_threads_by_agent = active_threads_by_agent
        self.spawn_calls = []

    def list_active_threads_by_agent(self):
        return self._active_threads_by_agent

    async def spawn_for_thread(self, thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        self.spawn_calls.append(
            {
                "thread_id": thread_id,
                "agent_name": agent_name,
                "bypass_permissions": bypass_permissions,
                "workspace_root": workspace_root,
            }
        )
        self._active_threads_by_agent.setdefault(agent_name, []).append(thread_id)
        return True


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


def test_attached_api_allows_direct_message_to_thread_participant_when_target_is_inactive(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()

    thread = thread_store.create_thread(
        topic="Attached API direct delivery",
        protocol="debate",
        participants=["alice", "bob"],
        initial_phase="context",
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_threads_router(
            thread_store,
            protocol_store,
            event_bus,
            agent_runner=_FakeAgentRunner({"bob": [thread["thread_id"]]}),
        )
    )
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/messages",
        json={
            "fromAgent": "bob",
            "content": "alice only",
            "tldr": "direct ask",
            "deliveryMode": "direct",
            "targetAgents": ["alice"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["delivery_mode"] == "direct"
    assert payload["target_agents"] == ["alice"]


def test_spawn_agent_accepts_bypass_permissions_flag(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    agent_runner = _FakeAgentRunner({})

    thread = thread_store.create_thread(
        topic="Attached API spawn agent",
        protocol="debate",
        participants=["user"],
        initial_phase="context",
    )

    class _FakeAgentStore:
        def get_agent(self, name: str):
            if name == "alice":
                return {"name": "alice"}
            return None

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_threads_router(
            thread_store,
            protocol_store,
            event_bus,
            agent_store=_FakeAgentStore(),
            agent_runner=agent_runner,
        )
    )
    client = TestClient(app)

    response = client.post(
        f"/api/threads/{thread['thread_id']}/spawn-agent",
        json={"agentName": "alice", "bypassPermissions": True, "projectRoot": "/tmp/test-project"},
    )

    assert response.status_code == 200
    assert agent_runner.spawn_calls == [
        {
            "thread_id": thread["thread_id"],
            "agent_name": "alice",
            "bypass_permissions": True,
            "workspace_root": Path("/tmp/test-project"),
        }
    ]
