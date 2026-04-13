"""Runtime session ownership for thread/agent message delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeSessionStatus(StrEnum):
    IDLE = "idle"
    RECEIVED = "received"
    FAILED = "failed"
    DONE = "done"


@dataclass
class RuntimeSession:
    thread_id: str
    agent_name: str
    provider: str
    transport_mode: str = "oneshot"
    auth_mode: str | None = None
    gateway_mode: str | None = None
    gateway_route: str | None = None
    transport_capability: str | None = None
    continuity_mode: str | None = None
    launch_strategy: str | None = None
    last_transport_error: str | None = None
    status: RuntimeSessionStatus = RuntimeSessionStatus.IDLE
    fallback_mode: str | None = None
    last_activity_at: str = field(default_factory=_now_iso)
    bypass_permissions: bool = False
    invocation_count: int = 0
    created_at: str = field(default_factory=_now_iso)
    last_invoked_at: str = ""
    provider_session_id: str | None = None

    @property
    def cli_session_id(self) -> str | None:
        return self.provider_session_id

    @cli_session_id.setter
    def cli_session_id(self, value: str | None) -> None:
        self.provider_session_id = value


@dataclass
class SessionDeliveryResult:
    ok: bool
    response_text: str = ""
    provider_session_id: str | None = None
    fallback_mode: str | None = None


DeliverRuntimeSession = Callable[[RuntimeSession, str], Awaitable[SessionDeliveryResult]]


class SessionSupervisor:
    """Own runtime sessions keyed by (thread_id, agent_name)."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], RuntimeSession] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @property
    def sessions(self) -> dict[tuple[str, str], RuntimeSession]:
        return self._sessions

    @property
    def locks(self) -> dict[tuple[str, str], asyncio.Lock]:
        return self._locks

    async def ensure_session(
        self,
        thread_id: str,
        agent_name: str,
        *,
        provider: str,
        transport_mode: str = "oneshot",
        bypass_permissions: bool = False,
    ) -> RuntimeSession:
        return self.ensure_session_nowait(
            thread_id,
            agent_name,
            provider=provider,
            transport_mode=transport_mode,
            bypass_permissions=bypass_permissions,
        )

    def ensure_session_nowait(
        self,
        thread_id: str,
        agent_name: str,
        *,
        provider: str,
        transport_mode: str = "oneshot",
        bypass_permissions: bool = False,
    ) -> RuntimeSession:
        key = (thread_id, agent_name)
        session = self._sessions.get(key)
        if session is not None:
            return session
        session = RuntimeSession(
            thread_id=thread_id,
            agent_name=agent_name,
            provider=provider,
            transport_mode=transport_mode,
            bypass_permissions=bypass_permissions,
        )
        self._sessions[key] = session
        return session

    def get_session(self, thread_id: str, agent_name: str) -> RuntimeSession | None:
        return self._sessions.get((thread_id, agent_name))

    async def deliver_message(
        self,
        thread_id: str,
        agent_name: str,
        prompt: str,
        *,
        deliver: DeliverRuntimeSession,
    ) -> SessionDeliveryResult:
        key = (thread_id, agent_name)
        session = self._sessions[key]
        lock = self._locks.setdefault(key, asyncio.Lock())

        async with lock:
            session.status = RuntimeSessionStatus.RECEIVED
            session.last_activity_at = _now_iso()
            result = await deliver(session, prompt)
            if result.provider_session_id:
                session.provider_session_id = result.provider_session_id
            if result.ok:
                session.status = RuntimeSessionStatus.DONE
            else:
                session.status = RuntimeSessionStatus.FAILED
                if result.fallback_mode:
                    session.fallback_mode = result.fallback_mode
            session.last_activity_at = _now_iso()
            return result

    async def close_thread_sessions(self, thread_id: str) -> int:
        return self.close_thread_sessions_nowait(thread_id)

    def close_thread_sessions_nowait(self, thread_id: str) -> int:
        keys_to_remove = [key for key in self._sessions if key[0] == thread_id]
        for key in keys_to_remove:
            self._sessions.pop(key, None)
            self._locks.pop(key, None)
        return len(keys_to_remove)
