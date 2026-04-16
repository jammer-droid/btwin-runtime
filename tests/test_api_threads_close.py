from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from btwin_cli.api_threads import create_threads_router
from btwin_core.event_bus import EventBus
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolSection, ProtocolStore
from btwin_core.thread_store import ThreadStore


class _FailingBacklinkTwin:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def record(self, content: str, topic: str, tags: list[str], tldr: str) -> dict[str, str]:
        record_id = "entry-thread-result-001"
        path = self.output_dir / f"{record_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            f"record_id: {record_id}\n"
            "tags:\n"
            "- thread-result\n"
            f"- protocol:debate\n"
            "---\n\n"
            f"{content}\n",
            encoding="utf-8",
        )
        return {"path": str(path)}

    def update_entry(self, **kwargs):
        return {"ok": False, "record_id": kwargs["record_id"], "error": "backlink failed"}


def test_close_thread_does_not_return_result_id_when_backlink_update_fails(tmp_path):
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(data_dir / "threads")
    protocol_store = ProtocolStore(data_dir / "protocols")
    thread = thread_store.create_thread(
        topic="Close regression",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )

    app = FastAPI()
    app.include_router(
        create_threads_router(
            thread_store,
            protocol_store,
            EventBus(),
            btwin_factory=lambda: _FailingBacklinkTwin(data_dir / "entries" / "entry"),
        )
    )

    client = TestClient(app)
    response = client.post(
        f"/api/threads/{thread['thread_id']}/close",
        json={"summary": "done", "decision": "merge"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert "result_record_id" not in payload


def test_close_thread_rejects_when_protocol_next_step_is_not_close(tmp_path):
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(data_dir / "threads")
    protocol_store = ProtocolStore(data_dir / "protocols")
    protocol_store.save_protocol(
        Protocol(
            name="review-flow",
            description="Requires a followup phase before close",
            phases=[
                ProtocolPhase(
                    name="context",
                    actions=["contribute"],
                    template=[ProtocolSection(section="background", required=True)],
                ),
                ProtocolPhase(name="discussion", actions=["discuss"]),
            ],
        )
    )
    thread = thread_store.create_thread(
        topic="Close regression",
        protocol="review-flow",
        participants=["alice"],
        initial_phase="context",
    )
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "context",
        content="## background\nDone.\n",
        tldr="ready",
    )

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, EventBus()))

    client = TestClient(app)
    response = client.post(
        f"/api/threads/{thread['thread_id']}/close",
        json={"summary": "done", "decision": "merge"},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "thread_not_closable_from_phase"
    assert "protocol apply-next" in detail["hint"]
