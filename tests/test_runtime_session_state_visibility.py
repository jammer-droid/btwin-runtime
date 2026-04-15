from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from btwin_cli.api_threads import create_threads_router
from btwin_core.agent_runner import AgentRunner, LaunchResolution
from btwin_core.agent_store import AgentStore
from btwin_core.auth_adapters import ResolvedLaunchAuth
from btwin_core.config import BTwinConfig
from btwin_core.event_bus import EventBus, SSEEvent
from btwin_core.message_router import RouteDecision
from btwin_core.protocol_store import ProtocolStore
from btwin_core.providers import CLIProvider, StreamEvent
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
            raise RuntimeError("live transport boom")

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


class _FakeSubprocessStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self) -> _FakeSubprocessStdout:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            await asyncio.sleep(0)
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeSubprocessStderr:
    async def read(self) -> bytes:
        return b"subprocess failed"


class _FakeSubprocess:
    pid = 4321

    def __init__(self, stdout_lines: list[bytes], returncode: int = 1) -> None:
        self.stdout = _FakeSubprocessStdout(stdout_lines)
        self.stderr = _FakeSubprocessStderr()
        self.stdin = None
        self.killed = False
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class _FakeStreamingProvider(CLIProvider):
    @property
    def name(self) -> str:
        return "codex"

    def build_command(self, session_id: str | None, bypass_permissions: bool) -> list[str]:
        del session_id, bypass_permissions
        return ["fake-codex"]

    def parse_stream_line(self, line: str) -> StreamEvent | None:
        if line.strip():
            return StreamEvent(event_type="assistant", text_delta="typing", raw={"line": line})
        return None

    def parse_final_response(self, output: str) -> str:
        return output.strip()

    def parse_session_id_from_output(self, output: str) -> str | None:
        del output
        return None

    def env_overrides(self, launch_auth=None) -> dict[str, str]:
        del launch_auth
        return {}


def _drain_events(queue: asyncio.Queue[SSEEvent]) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return events


def _build_runner(tmp_path: Path) -> tuple[AgentRunner, EventBus]:
    data_dir = tmp_path / "data"
    threads_dir = data_dir / "threads"
    threads_dir.mkdir(parents=True)
    event_bus = EventBus()
    runner = AgentRunner(
        ThreadStore(threads_dir),
        ProtocolStore(data_dir / "protocols"),
        AgentStore(data_dir),
        event_bus,
        config=BTwinConfig(data_dir=data_dir),
    )
    return runner, event_bus


def _install_runtime_event_enricher(
    tmp_path: Path,
    runner: AgentRunner,
    event_bus: EventBus,
) -> None:
    thread_store = ThreadStore(tmp_path / "threads")
    protocol_store = ProtocolStore(tmp_path / "protocols")
    create_threads_router(thread_store, protocol_store, event_bus, agent_runner=runner)


@pytest.mark.asyncio
async def test_failed_live_transport_event_includes_transport_error_before_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, event_bus = _build_runner(tmp_path)
    _install_runtime_event_enricher(tmp_path, runner, event_bus)
    event_queue = event_bus.subscribe()

    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="live_process_transport",
        fallback_mode="resume_invocation_transport",
    )
    runner._sessions[("thread-123", "agent-1")] = session

    adapter = _FakeLiveTransportAdapter(
        [
            SessionEvent(kind="turn_started", content="turn-1"),
            SessionEvent(kind="text_delta", content="live typing"),
        ]
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.build_transport_for_provider",
        lambda *args, **kwargs: _FakeLiveTransport(adapter),
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.AgentRunner._resolve_launch_resolution",
        lambda self, runtime_session: LaunchResolution(
            provider=_FakeStreamingProvider(),
            auth=ResolvedLaunchAuth(
                provider_name="codex",
                mode="cli_environment",
            ),
            env={},
            metadata={},
        ),
    )

    success_proc = _FakeSubprocess([b"fallback typing\n"], returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args, kwargs
        return success_proc

    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await runner.invoke("thread-123", "agent-1", "prompt text")

    events = _drain_events(event_queue)
    failed_events = [
        event
        for event in events
        if event.type == "agent_session_state" and event.metadata and event.metadata.get("state") == "failed"
    ]
    fallback_events = [
        event
        for event in events
        if event.type == "agent_session_state" and event.metadata and event.metadata.get("state") == "fallback"
    ]

    assert result.ok is True
    assert failed_events
    assert failed_events[0].metadata["last_transport_error"] == "live transport boom"
    assert fallback_events
    assert fallback_events[0].metadata["last_transport_error"] == "live transport boom"


@pytest.mark.asyncio
async def test_failed_live_transport_without_fallback_includes_transport_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, event_bus = _build_runner(tmp_path)
    _install_runtime_event_enricher(tmp_path, runner, event_bus)
    event_queue = event_bus.subscribe()

    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="live_process_transport",
    )
    runner._sessions[("thread-123", "agent-1")] = session

    adapter = _FakeLiveTransportAdapter(
        [
            SessionEvent(kind="turn_started", content="turn-1"),
            SessionEvent(kind="text_delta", content="live typing"),
        ]
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.build_transport_for_provider",
        lambda *args, **kwargs: _FakeLiveTransport(adapter),
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.AgentRunner._resolve_launch_resolution",
        lambda self, runtime_session: LaunchResolution(
            provider=_FakeStreamingProvider(),
            auth=ResolvedLaunchAuth(
                provider_name="codex",
                mode="cli_environment",
            ),
            env={},
            metadata={},
        ),
    )

    result = await runner.invoke("thread-123", "agent-1", "prompt text")

    events = _drain_events(event_queue)
    failed_events = [
        event
        for event in events
        if event.type == "agent_session_state" and event.metadata and event.metadata.get("state") == "failed"
    ]

    assert result.ok is False
    assert failed_events
    assert failed_events[0].metadata["last_transport_error"] == "live transport boom"


@pytest.mark.asyncio
async def test_recover_for_thread_starts_primary_transport_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _event_bus = _build_runner(tmp_path)
    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="resume_invocation_transport",
        primary_transport_mode="live_process_transport",
        fallback_mode="resume_invocation_transport",
        degraded=True,
        recoverable=True,
        recovery_attempts=0,
        last_transport_error="live transport timed out",
        provider_session_id="thread-existing",
    )
    runner._sessions[("thread-123", "agent-1")] = session

    scheduled: list[tuple[str, str]] = []

    async def fake_background_spawn(thread_id: str, agent_name: str) -> None:
        scheduled.append((thread_id, agent_name))

    created_tasks = []

    def fake_create_task(coro):  # noqa: ANN001
        created_tasks.append(coro)
        return object()

    monkeypatch.setattr(runner, "_background_spawn", fake_background_spawn)
    monkeypatch.setattr("btwin_core.agent_runner.asyncio.create_task", fake_create_task)

    result = await runner.recover_for_thread("thread-123", "agent-1")

    assert result == {
        "thread_id": "thread-123",
        "agent_name": "agent-1",
        "provider": "codex",
        "primary_transport_mode": "live_process_transport",
        "transport_mode": "resume_invocation_transport",
        "fallback_mode": "resume_invocation_transport",
        "status": "idle",
        "degraded": True,
        "recoverable": False,
        "recovery_attempts": 1,
        "recovery_pending": True,
        "recovery_target_transport_mode": "live_process_transport",
        "last_transport_error": "live transport timed out",
        "recovery_started": True,
    }
    assert ("thread-123", "agent-1") in runner._managed_sessions
    assert session.provider_session_id == "thread-existing"
    assert session.connect_only_bootstrap is True
    assert len(created_tasks) == 1
    await created_tasks[0]
    assert scheduled == [("thread-123", "agent-1")]


@pytest.mark.asyncio
async def test_invoke_logs_runtime_recovery_succeeded_after_live_recover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _event_bus = _build_runner(tmp_path)
    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="live_process_transport",
        primary_transport_mode="live_process_transport",
    )
    session.recovery_pending = True
    runner._sessions[("thread-123", "agent-1")] = session

    adapter = _FakeLiveTransportAdapter(
        [
            SessionEvent(kind="turn_started", content="turn-1"),
            SessionEvent(kind="item.completed", content="recovered", metadata={"source": "codex"}),
        ]
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.build_transport_for_provider",
        lambda *args, **kwargs: _FakeLiveTransport(adapter),
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.AgentRunner._resolve_launch_resolution",
        lambda self, runtime_session: LaunchResolution(
            provider=_FakeStreamingProvider(),
            auth=ResolvedLaunchAuth(
                provider_name="codex",
                mode="cli_environment",
            ),
            env={},
            metadata={},
        ),
    )

    logged: list[str] = []

    def capture_log(event_type: str, **kwargs):  # noqa: ANN001
        logged.append(event_type)

    monkeypatch.setattr(runner, "_log_runtime_event", capture_log)

    result = await runner.invoke("thread-123", "agent-1", "prompt text")

    assert result.ok is True
    assert "runtime_recovery_succeeded" in logged
    assert session.transport_mode == "live_process_transport"
    assert session.degraded is False
    assert session.recoverable is False
    assert session.recovery_pending is False
    assert session.recovery_target_transport_mode is None


@pytest.mark.asyncio
async def test_invoke_logs_runtime_recovery_failed_when_recover_falls_back_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _event_bus = _build_runner(tmp_path)
    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="live_process_transport",
        primary_transport_mode="live_process_transport",
        fallback_mode="resume_invocation_transport",
    )
    session.recovery_pending = True
    runner._sessions[("thread-123", "agent-1")] = session

    adapter = _FakeLiveTransportAdapter(
        [
            SessionEvent(kind="turn_started", content="turn-1"),
            SessionEvent(kind="text_delta", content="live typing"),
        ]
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.build_transport_for_provider",
        lambda *args, **kwargs: _FakeLiveTransport(adapter),
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.AgentRunner._resolve_launch_resolution",
        lambda self, runtime_session: LaunchResolution(
            provider=_FakeStreamingProvider(),
            auth=ResolvedLaunchAuth(
                provider_name="codex",
                mode="cli_environment",
            ),
            env={},
            metadata={},
        ),
    )

    success_proc = _FakeSubprocess([b"fallback typing\n"], returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args, kwargs
        return success_proc

    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    logged: list[str] = []

    def capture_log(event_type: str, **kwargs):  # noqa: ANN001
        logged.append(event_type)

    monkeypatch.setattr(runner, "_log_runtime_event", capture_log)

    result = await runner.invoke("thread-123", "agent-1", "prompt text")

    assert result.ok is True
    assert "runtime_recovery_failed" in logged
    assert session.transport_mode == "resume_invocation_transport"
    assert session.recovery_pending is False


@pytest.mark.asyncio
async def test_direct_message_resumes_inactive_thread_participant_from_thread_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, event_bus = _build_runner(tmp_path)
    _install_runtime_event_enricher(tmp_path, runner, event_bus)

    runner._agents.register("alice", model="gpt-5", provider="codex")
    thread = runner._threads.create_thread(
        topic="Resume inactive participant",
        protocol="debate",
        participants=["user", "alice"],
        initial_phase="context",
    )

    monkeypatch.setattr(
        runner._message_router,
        "route",
        lambda **kwargs: RouteDecision(
            mode="direct",
            targets=["alice"],
            source="deterministic",
            reason="explicit_target_selection",
        ),
    )

    invoke_calls: list[tuple[str, str, str]] = []
    created_tasks = []

    async def fake_invoke(thread_id: str, agent_name: str, prompt: str):  # noqa: ANN202
        invoke_calls.append((thread_id, agent_name, prompt))
        return type("Result", (), {"ok": True, "response_text": "resumed reply"})()

    monkeypatch.setattr(runner, "invoke", fake_invoke)
    monkeypatch.setattr(runner, "_should_use_live_transport", lambda session: True)
    monkeypatch.setattr(
        runner,
        "_connect_live_transport_only",
        lambda session, *, thread_id, agent_name: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_task",
        lambda coro: created_tasks.append(coro) or object(),
    )

    await runner._handle_message(
        SSEEvent(
            type="message_sent",
            resource_id=thread["thread_id"],
            metadata={
                "from_agent": "user",
                "content": "Please continue from the saved context.",
                "delivery_mode": "direct",
                "target_agents": ["alice"],
                "chain_depth": 0,
                "message_id": "msg-1",
            },
        )
    )

    assert len(created_tasks) == 1
    await created_tasks[0]
    session = runner.get_runtime_session_status(thread["thread_id"], "alice")
    assert session is not None
    assert (thread["thread_id"], "alice") in runner._managed_sessions
    assert len(invoke_calls) == 1
    assert "## Thread: Resume inactive participant" in invoke_calls[0][2]
    assert "Current ask:" in invoke_calls[0][2]
    assert "Please continue from the saved context." in invoke_calls[0][2]
