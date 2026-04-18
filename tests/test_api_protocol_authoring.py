from fastapi import FastAPI
from fastapi.testclient import TestClient

from btwin_cli.api_threads import create_threads_router
from btwin_core.event_bus import EventBus
from btwin_core.protocol_store import ProtocolStore
from btwin_core.thread_store import ThreadStore


def _authoring_protocol_payload() -> dict[str, object]:
    return {
        "name": "review-loop",
        "description": "Authoring-first review loop",
        "phases": [
            {
                "name": "review",
                "actions": ["contribute"],
                "gate": "review-gate",
                "outcome_policy": "review-outcomes",
            },
            {
                "name": "decision",
                "actions": ["decide"],
                "decided_by": "user",
            },
        ],
        "gates": [
            {
                "name": "review-gate",
                "routes": [
                    {"outcome": "retry", "target_phase": "review", "alias": "Retry Loop", "key": "retry-loop"},
                    {"outcome": "accept", "target_phase": "decision", "alias": "Accept Gate", "key": "accept-gate"},
                ],
            }
        ],
        "outcome_policies": [
            {
                "name": "review-outcomes",
                "emitters": ["reviewer", "user"],
                "actions": ["decide"],
                "outcomes": ["retry", "accept"],
            }
        ],
    }


def test_protocol_authoring_api_create_compiles_before_save(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post("/api/protocols", json=_authoring_protocol_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["transitions"] == [
        {
            "from": "review",
            "to": "review",
            "on": "retry",
            "alias": "Retry Loop",
            "key": "retry-loop",
        },
        {
            "from": "review",
            "to": "decision",
            "on": "accept",
            "alias": "Accept Gate",
            "key": "accept-gate",
        },
    ]


def test_protocol_authoring_api_preview_returns_authoring_summary_and_compiled_payload(tmp_path):
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    event_bus = EventBus()

    app = FastAPI()
    app.include_router(create_threads_router(thread_store, protocol_store, event_bus))
    client = TestClient(app)

    response = client.post("/api/protocols/preview", json=_authoring_protocol_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["authoring"] == {
        "name": "review-loop",
        "phase_count": 2,
        "gate_count": 1,
        "outcome_policy_count": 1,
    }
    assert payload["compiled"]["outcomes"] == ["retry", "accept"]
