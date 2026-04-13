from __future__ import annotations

from typing import AsyncIterator, Protocol

from btwin_core.prototypes.persistent_sessions.types import (
    SessionCloseResult,
    SessionConfig,
    SessionEvent,
    SessionHealth,
    SessionStartResult,
    SessionTurn,
)


class PersistentSessionAdapter(Protocol):
    async def start(self, config: SessionConfig) -> SessionStartResult: ...

    async def send_turn(self, turn: SessionTurn) -> None: ...

    def read_events(self) -> AsyncIterator[SessionEvent]: ...

    async def health_check(self) -> SessionHealth: ...

    async def close(self) -> SessionCloseResult: ...
