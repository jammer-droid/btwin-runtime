from __future__ import annotations

from pathlib import Path

from btwin_core.agent_runner import AgentRunner, InvocationResult, RuntimeOutput
from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig
from btwin_core.event_bus import EventBus
from btwin_core.protocol_store import ProtocolStore
from btwin_core.thread_store import ThreadStore
from btwin_core.validation_telemetry import ValidationTelemetryStore


def test_validation_telemetry_store_writes_core_event(tmp_path: Path) -> None:
    store = ValidationTelemetryStore(tmp_path)

    event = store.record(
        "validation.signal.recorded",
        thread_id="thread-1",
        agent_name="alice",
        phase="analysis",
        visibility="internal",
        evidence_level="critical",
        payload={"signal": "contribution_submitted", "message_phase": "final_answer"},
    )

    assert event["event_type"] == "validation.signal.recorded"
    assert event["thread_id"] == "thread-1"
    assert event["payload"]["signal"] == "contribution_submitted"
    assert store.file_path == tmp_path / "logs" / "validation-telemetry.jsonl"


def test_validation_telemetry_store_filters_by_thread_and_level(tmp_path: Path) -> None:
    store = ValidationTelemetryStore(tmp_path)
    store.record(
        "validation.signal.recorded",
        thread_id="thread-1",
        agent_name="alice",
        phase="analysis",
        visibility="internal",
        evidence_level="critical",
        payload={"signal": "contribution_submitted"},
    )
    store.record(
        "validation.signal.recorded",
        thread_id="thread-2",
        agent_name="bob",
        phase="discussion",
        visibility="internal",
        evidence_level="quality",
        payload={"signal": "protocol_next_called"},
    )

    rows = store.tail(limit=10, thread_id="thread-1", evidence_level="critical")

    assert len(rows) == 1
    assert rows[0]["thread_id"] == "thread-1"
    assert rows[0]["payload"]["signal"] == "contribution_submitted"


def test_agent_runner_records_session_state_signal(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    threads_dir = data_dir / "threads"
    threads_dir.mkdir(parents=True)

    thread_store = ThreadStore(threads_dir)
    thread = thread_store.create_thread(
        topic="Telemetry thread",
        protocol="code-review",
        participants=["alice"],
        initial_phase="analysis",
    )
    runner = AgentRunner(
        thread_store,
        ProtocolStore(data_dir / "protocols"),
        AgentStore(data_dir),
        EventBus(),
        config=BTwinConfig(data_dir=data_dir),
    )

    runner._emit_session_state(thread["thread_id"], "alice", "thinking", reason="started")

    rows = ValidationTelemetryStore(data_dir).tail(limit=10, thread_id=thread["thread_id"])

    assert len(rows) == 1
    assert rows[0]["event_type"] == "validation.signal.recorded"
    assert rows[0]["agent_name"] == "alice"
    assert rows[0]["phase"] == "analysis"
    assert rows[0]["payload"]["signal"] == "session_state_changed"
    assert rows[0]["payload"]["state"] == "thinking"
    assert rows[0]["payload"]["reason"] == "started"


def test_agent_runner_records_runtime_and_fallback_output_signals(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    threads_dir = data_dir / "threads"
    threads_dir.mkdir(parents=True)

    thread_store = ThreadStore(threads_dir)
    thread = thread_store.create_thread(
        topic="Telemetry thread",
        protocol="code-review",
        participants=["alice"],
        initial_phase="analysis",
    )
    runner = AgentRunner(
        thread_store,
        ProtocolStore(data_dir / "protocols"),
        AgentStore(data_dir),
        EventBus(),
        config=BTwinConfig(data_dir=data_dir),
    )

    runner._persist_invocation_outputs(
        thread["thread_id"],
        "alice",
        InvocationResult(
            ok=True,
            outputs=(RuntimeOutput(content="Final answer", phase="final_answer", state_affecting=True),),
        ),
        chain_depth=1,
    )
    runner._persist_invocation_outputs(
        thread["thread_id"],
        "alice",
        InvocationResult(ok=True, response_text="Fallback final answer"),
        chain_depth=1,
    )

    signals = [
        row["payload"]["signal"]
        for row in ValidationTelemetryStore(data_dir).tail(limit=10, thread_id=thread["thread_id"])
    ]

    assert "runtime_output_persisted" in signals
    assert "fallback_runtime_output_persisted" in signals
    assert "message_persisted" in signals
