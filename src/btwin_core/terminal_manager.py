"""Terminal session manager for CLI process lifecycle."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from btwin_core.process_adapter import ProcessAdapter, PtyAdapter


@dataclass
class TerminalSession:
    session_id: str
    agent_name: str
    adapter: ProcessAdapter
    command: str
    args: list[str]
    created_at: str
    status: str = "running"


class TerminalManager:
    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}

    async def create_session(
        self, agent_name: str, command: str, args: list[str],
        cwd: str | None = None, env: dict[str, str] | None = None,
    ) -> TerminalSession:
        adapter = PtyAdapter()
        await adapter.spawn(command, args, cwd=cwd, env=env)
        session_id = f"term-{uuid.uuid4().hex[:12]}"
        session = TerminalSession(
            session_id=session_id,
            agent_name=agent_name,
            adapter=adapter,
            command=command,
            args=args,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> TerminalSession | None:
        return self._sessions.get(session_id)

    def kill_session(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.adapter.kill()
        session.status = "stopped"
        return True

    def list_sessions(self) -> list[TerminalSession]:
        return list(self._sessions.values())

    def cleanup(self) -> None:
        for session in self._sessions.values():
            if session.status == "running":
                session.adapter.kill()
                session.status = "stopped"
