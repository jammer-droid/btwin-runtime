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
        self.recover_calls = []
        self.attach_or_resume_calls = []
        self.session_status = None

    def list_active_threads_by_agent(self):
        return self._active_threads_by_agent

    def get_runtime_session_status(self, thread_id, agent_name):
        del thread_id, agent_name
        return self.session_status

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

    async def recover_for_thread(self, thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        self.recover_calls.append(
            {
                "thread_id": thread_id,
                "agent_name": agent_name,
                "bypass_permissions": bypass_permissions,
                "workspace_root": workspace_root,
            }
        )
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "recoverable": True,
            "transport_mode": "live_process_transport",
            "recovery_started": True,
        }

    async def attach_or_resume_for_thread(self, thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        self.attach_or_resume_calls.append(
            {
                "thread_id": thread_id,
                "agent_name": agent_name,
                "bypass_permissions": bypass_permissions,
                "workspace_root": workspace_root,
            }
        )
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "primary_transport_mode": "live_process_transport",
            "transport_mode": "resume_invocation_transport",
            "recovery_started": True,
            "reused_session": False,
            "resumed_from_state": False,
        }


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
    assert agent_runner.attach_or_resume_calls == [
        {
            "thread_id": thread["thread_id"],
            "agent_name": "alice",
            "bypass_permissions": True,
            "workspace_root": Path("/tmp/test-project"),
        }
    ]


def test_spawn_agent_uses_attach_or_resume_for_recoverable_existing_session(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    agent_runner = _FakeAgentRunner({})
    agent_runner.session_status = {
        "thread_id": "thread-1",
        "agent_name": "alice",
        "primary_transport_mode": "live_process_transport",
        "transport_mode": "resume_invocation_transport",
        "recoverable": True,
        "degraded": True,
        "recovery_attempts": 1,
    }

    thread = thread_store.create_thread(
        topic="Attached API duplicate attach",
        protocol="debate",
        participants=["user", "alice"],
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
        json={"agentName": "alice", "projectRoot": "/tmp/test-project"},
    )

    assert response.status_code == 200
    assert agent_runner.attach_or_resume_calls == [
        {
            "thread_id": thread["thread_id"],
            "agent_name": "alice",
            "bypass_permissions": None,
            "workspace_root": Path("/tmp/test-project"),
        }
    ]
    assert response.json()["recovery_started"] is True


def test_recover_agent_uses_agent_runner_recovery_path(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()
    agent_runner = _FakeAgentRunner({})

    thread = thread_store.create_thread(
        topic="Attached API recover agent",
        protocol="debate",
        participants=["alice"],
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
        f"/api/threads/{thread['thread_id']}/recover-agent",
        json={"agentName": "alice", "bypassPermissions": True, "projectRoot": "/tmp/test-project"},
    )

    assert response.status_code == 200
    assert response.json()["recovery_started"] is True
    assert agent_runner.recover_calls == [
        {
            "thread_id": thread["thread_id"],
            "agent_name": "alice",
            "bypass_permissions": True,
            "workspace_root": Path("/tmp/test-project"),
        }
    ]
