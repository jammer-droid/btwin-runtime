"""REST API for terminal session management."""

from __future__ import annotations

import asyncio
import base64
import json as _json
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from btwin_core.agent_store import AgentStore
from btwin_core.resource_paths import resolve_bundled_providers_path
from btwin_core.terminal_manager import TerminalManager


class CreateTerminalRequest(BaseModel):
    agent_name: str
    command: str | None = None
    args: list[str] | None = None
    cwd: str | None = None


def _load_providers(data_dir: Path) -> dict:
    config_path = data_dir / "providers.json"
    if config_path.exists():
        return _json.loads(config_path.read_text(encoding="utf-8"))
    bundled = resolve_bundled_providers_path()
    if bundled is not None:
        return _json.loads(bundled.read_text(encoding="utf-8"))
    return {"providers": [], "capabilities": []}


def create_terminal_router(terminal_manager: TerminalManager, storage=None) -> APIRouter:
    router = APIRouter()

    @router.post("/api/terminals")
    async def create_terminal(body: CreateTerminalRequest):
        command = body.command
        args = list(body.args or [])

        if command is None and storage is not None:
            agent_store = AgentStore(storage.data_dir)
            agent = agent_store.get_agent(body.agent_name)
            if agent:
                model_id = agent.get("model", "")
                providers_config = _load_providers(storage.data_dir)
                for provider in providers_config.get("providers", []):
                    for model in provider.get("models", []):
                        if model["id"] == model_id:
                            command = provider["cli"]
                            args = list(provider.get("default_args", []))
                            reasoning_level = agent.get("reasoning_level")
                            reasoning_arg = provider.get("reasoning_arg")
                            if reasoning_level and reasoning_arg:
                                expanded = reasoning_arg.replace("{level}", reasoning_level)
                                args.extend(expanded.split())
                            if agent.get("bypass_permissions", False):
                                if provider["cli"] == "claude":
                                    args.append("--dangerously-skip-permissions")
                                elif provider["cli"] == "codex":
                                    args.append("--full-auto")
                            break
                    if command:
                        break

        if command is None:
            command = "echo"
            args = ["No CLI configured for this agent"]

        session = await terminal_manager.create_session(body.agent_name, command, args, cwd=body.cwd)
        return {
            "session_id": session.session_id,
            "agent_name": session.agent_name,
            "command": session.command,
            "args": session.args,
            "status": session.status,
            "created_at": session.created_at,
        }

    @router.get("/api/terminals")
    def list_terminals():
        sessions = terminal_manager.list_sessions()
        return {
            "sessions": [
                {
                    "session_id": session.session_id,
                    "agent_name": session.agent_name,
                    "command": session.command,
                    "status": session.status,
                    "created_at": session.created_at,
                }
                for session in sessions
            ]
        }

    @router.delete("/api/terminals/{session_id}")
    def kill_terminal(session_id: str):
        result = terminal_manager.kill_session(session_id)
        if not result:
            return JSONResponse(status_code=404, content={"error": "NOT_FOUND"})
        return {"killed": True}

    @router.websocket("/ws/terminal/{session_id}")
    async def terminal_ws(websocket: WebSocket, session_id: str):
        session = terminal_manager.get_session(session_id)
        if session is None or session.status != "running":
            await websocket.close(code=4004, reason="Session not found or stopped")
            return

        await websocket.accept()
        adapter = session.adapter

        async def read_pty():
            while session.status == "running" and adapter.is_alive():
                try:
                    data = await adapter.read()
                    if data:
                        await websocket.send_json(
                            {
                                "type": "output",
                                "data": base64.b64encode(data).decode("ascii"),
                            }
                        )
                    else:
                        await asyncio.sleep(0.05)
                except Exception:
                    break
            try:
                await websocket.send_json({"type": "exit", "code": 0})
            except Exception:
                pass

        read_task = asyncio.create_task(read_pty())

        try:
            while True:
                msg = await websocket.receive_json()
                if msg["type"] == "input":
                    await adapter.write(msg["data"].encode("utf-8"))
                elif msg["type"] == "resize":
                    await adapter.resize(msg["rows"], msg["cols"])
        except WebSocketDisconnect:
            pass
        finally:
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass

    return router
