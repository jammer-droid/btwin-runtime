from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from btwin_core.agent_runner import AgentRunner, LaunchResolution
from btwin_core.agent_store import AgentStore
from btwin_core.auth_adapters import ResolvedLaunchAuth
from btwin_core.config import BTwinConfig
from btwin_core.event_bus import EventBus
from btwin_core.protocol_store import ProtocolStore
from btwin_core.providers import CodexProvider
from btwin_core.prototypes.persistent_sessions.types import (
    SessionCloseResult,
    SessionConfig,
    SessionEvent,
    SessionHealth,
    SessionStartResult,
    SessionTurn,
)
from btwin_core.session_supervisor import RuntimeSession
from btwin_core.thread_store import ThreadStore


class _FakeLiveTransportAdapter:
    def __init__(self, events: list[SessionEvent]) -> None:
        self._events = events
        self.started_with: SessionConfig | None = None
        self.sent_turns: list[SessionTurn] = []
        self.closed = False

    async def start(self, config: SessionConfig) -> SessionStartResult:
        self.started_with = config
        return SessionStartResult(
            session_id="thread-abc",
            metadata={"ok": True, "provider": "codex"},
        )

    async def send_turn(self, turn: SessionTurn) -> None:
        self.sent_turns.append(turn)

    def read_events(self) -> AsyncIterator[SessionEvent]:
        async def iterator() -> AsyncIterator[SessionEvent]:
            for event in self._events:
                yield event

        return iterator()

    async def health_check(self) -> SessionHealth:
        return SessionHealth(ok=True)

    async def close(self) -> SessionCloseResult:
        self.closed = True
        return SessionCloseResult(ok=True)


class _FakeLiveTransport:
    mode = "live_process_transport"
    fallback_mode = None
    requires_health_check_before_reuse = False
    supports_resume_fallback = False

    def __init__(self, adapter: _FakeLiveTransportAdapter) -> None:
        self._adapter = adapter

    def build_adapter(self, launch_context=None) -> _FakeLiveTransportAdapter:
        del launch_context
        return self._adapter

    def build_session_config(self, launch_context=None, *, resume_session_id=None) -> SessionConfig:
        del launch_context, resume_session_id
        return SessionConfig()


@pytest.mark.asyncio
async def test_live_transport_accepts_codex_item_completed_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    threads_dir = data_dir / "threads"
    threads_dir.mkdir(parents=True)

    runner = AgentRunner(
        ThreadStore(threads_dir),
        ProtocolStore(data_dir / "protocols"),
        AgentStore(data_dir),
        EventBus(),
        config=BTwinConfig(data_dir=data_dir),
    )

    adapter = _FakeLiveTransportAdapter(
        [
            SessionEvent(kind="turn_started", content="turn-1"),
            SessionEvent(kind="item.completed", content="Hello from Codex", metadata={"source": "codex"}),
        ]
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.build_transport_for_provider",
        lambda *args, **kwargs: _FakeLiveTransport(adapter),
    )

    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="live_process_transport",
    )
    launch = LaunchResolution(
        provider=CodexProvider(),
        auth=ResolvedLaunchAuth(
            provider_name="codex",
            mode="cli_environment",
        ),
        env={},
        metadata={},
    )

    result = await runner._run_live_transport(
        session,
        "prompt text",
        launch,
        thread_id="thread-123",
        agent_name="agent-1",
    )

    assert result.ok is True
    assert result.response_text == "Hello from Codex"
    assert adapter.sent_turns[0].content == "prompt text"
    assert adapter.closed is False


def test_live_transport_timeout_policy_uses_startup_grace_for_first_turn(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner = AgentRunner(
        ThreadStore(data_dir / "threads"),
        ProtocolStore(data_dir / "protocols"),
        AgentStore(data_dir),
        EventBus(),
        config=BTwinConfig(data_dir=data_dir),
    )
    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="live_process_transport",
    )

    idle_timeout, turn_timeout = runner._live_transport_timeout_policy(session)

    assert idle_timeout == 180.0
    assert turn_timeout == 180.0


def test_live_transport_timeout_policy_disables_deadlines_after_startup_turn(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner = AgentRunner(
        ThreadStore(data_dir / "threads"),
        ProtocolStore(data_dir / "protocols"),
        AgentStore(data_dir),
        EventBus(),
        config=BTwinConfig(data_dir=data_dir),
    )
    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="live_process_transport",
        invocation_count=1,
    )

    idle_timeout, turn_timeout = runner._live_transport_timeout_policy(session)

    assert idle_timeout is None
    assert turn_timeout is None


def test_live_transport_timeout_policy_uses_startup_grace_for_recovery_turn(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner = AgentRunner(
        ThreadStore(data_dir / "threads"),
        ProtocolStore(data_dir / "protocols"),
        AgentStore(data_dir),
        EventBus(),
        config=BTwinConfig(data_dir=data_dir),
    )
    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="resume_invocation_transport",
        primary_transport_mode="live_process_transport",
        recovery_pending=True,
        recovery_target_transport_mode="live_process_transport",
        invocation_count=4,
    )

    idle_timeout, turn_timeout = runner._live_transport_timeout_policy(session)

    assert idle_timeout == 180.0
    assert turn_timeout == 180.0
