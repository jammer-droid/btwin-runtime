"""AgentStore — persistent agent registration via agents.json."""

from __future__ import annotations

import json
import logging
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_UNSET: Any = object()

logger = logging.getLogger(__name__)
_SAFE_AUTH_HINT_KEYS = ("mode", "token_ref")
_SECRET_CONFIG_KEYS = {
    "api_key",
    "api_secret",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
    "password",
    "secret",
    "token",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clone_cli_config(cli_config: dict | None) -> dict | None:
    if cli_config is None:
        return None
    return deepcopy(cli_config)


def _is_secret_like_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_").strip()
    if normalized in _SAFE_AUTH_HINT_KEYS:
        return False
    if normalized in _SECRET_CONFIG_KEYS:
        return True
    return normalized.endswith(("_secret", "_password", "_token"))


def _sanitize_public_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_secret_like_key(key):
                continue
            sanitized[key] = _sanitize_public_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_public_value(item) for item in value]
    return deepcopy(value)


def sanitize_cli_config_for_output(cli_config: dict | None) -> dict | None:
    """Return a public-safe cli_config copy without plaintext auth secrets."""
    if cli_config is None:
        return None

    sanitized = _clone_cli_config(cli_config)
    if sanitized is None:
        return None

    sanitized = _sanitize_public_value(sanitized)
    return sanitized if isinstance(sanitized, dict) else None


def sanitize_agent_for_output(agent: dict) -> dict:
    """Return a public-safe agent payload for API responses."""
    sanitized = deepcopy(agent)
    sanitized["cli_config"] = sanitize_cli_config_for_output(agent.get("cli_config"))
    return sanitized


class AgentStore:
    """Manage agent registrations in a JSON file."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._path = data_dir / "agents.json"

    def register(
        self,
        name: str,
        model: str,
        alias: str | None = None,
        capabilities: list[str] | None = None,
        cli_config: dict | None = None,
        reasoning_level: str | None = None,
        bypass_permissions: bool | None = None,
        memo: str | None = None,
        provider: str | None = None,
        role: str | None = None,
    ) -> dict:
        """Register or update an agent (upsert). Returns agent info dict."""
        agents = self._read()
        now = datetime.now(timezone.utc).isoformat()

        existing = agents.get(name)
        if existing is not None:
            existing["model"] = model
            existing["alias"] = alias
            existing["last_seen"] = now
            existing.setdefault("queue", [])
            existing.setdefault("capabilities", [model])
            if capabilities is not None:
                existing["capabilities"] = capabilities
            if cli_config is not None:
                existing["cli_config"] = _clone_cli_config(cli_config)
            existing["reasoning_level"] = reasoning_level
            if bypass_permissions is not None:
                existing["bypass_permissions"] = bypass_permissions
            existing["memo"] = memo or existing.get("memo", "")
            if provider is not None:
                existing["provider"] = provider
            if role is not None:
                existing["role"] = role
        else:
            agents[name] = {
                "model": model,
                "alias": alias,
                "registered_at": now,
                "last_seen": now,
                "queue": [],
                "capabilities": capabilities if capabilities is not None else [model],
                "cli_config": _clone_cli_config(cli_config),
                "reasoning_level": reasoning_level,
                "bypass_permissions": bypass_permissions if bypass_permissions is not None else False,
                "memo": memo or "",
                "provider": provider,
                "role": role,
            }

        self._write(agents)
        return {"name": name, **agents[name]}

    def update_agent(
        self,
        name: str,
        alias: str | None = None,
        capabilities: list[str] | None = None,
        cli_config: dict | None = None,
        model: str | None = None,
        reasoning_level: Any = _UNSET,
        bypass_permissions: bool | None = None,
        memo: Any = _UNSET,
        provider: Any = _UNSET,
        role: Any = _UNSET,
    ) -> dict | None:
        """Partially update an agent's fields. Returns updated agent info, or None if not found."""
        data = self._read()
        if name not in data:
            return None
        entry = data[name]
        if alias is not None:
            entry["alias"] = alias
        if capabilities is not None:
            entry["capabilities"] = capabilities
        if cli_config is not None:
            entry["cli_config"] = _clone_cli_config(cli_config)
        if model is not None:
            entry["model"] = model
        if reasoning_level is not _UNSET:
            entry["reasoning_level"] = reasoning_level
        if bypass_permissions is not None:
            entry["bypass_permissions"] = bypass_permissions
        if memo is not _UNSET:
            entry["memo"] = memo
        if provider is not _UNSET:
            entry["provider"] = provider
        if role is not _UNSET:
            entry["role"] = role
        entry["last_seen"] = _now_iso()
        self._write(data)
        return {"name": name, **entry}

    def unregister(self, name: str) -> bool:
        """Remove an agent. Returns False if not found."""
        agents = self._read()
        if name not in agents:
            return False
        del agents[name]
        self._write(agents)
        return True

    def list_agents(self) -> list[dict]:
        """Return all registered agents as list of dicts with 'name' key included."""
        agents = self._read()
        return [{"name": name, **info} for name, info in agents.items()]

    def get_agent(self, name: str) -> dict | None:
        """Get a single agent by name. Returns None if not found."""
        agents = self._read()
        info = agents.get(name)
        if info is None:
            return None
        return {"name": name, **info}

    def get_queue(self, name: str) -> list[dict]:
        """Return agent's queue. Empty list if agent not found."""
        agents = self._read()
        info = agents.get(name)
        if info is None:
            return []
        return info.get("queue", [])

    def enqueue_task(self, name: str, workflow_id: str, task_id: str) -> list[dict]:
        """Append task to agent's queue (FIFO). Returns updated queue."""
        agents = self._read()
        if name not in agents:
            raise ValueError(f"Agent not found: {name}")
        queue = agents[name].setdefault("queue", [])
        if any(task["task_id"] == task_id for task in queue):
            return queue
        queue.append({"workflow_id": workflow_id, "task_id": task_id})
        self._write(agents)
        return queue

    def dequeue_task(self, name: str, task_id: str) -> list[dict]:
        """Remove task from queue by task_id. Returns updated queue."""
        agents = self._read()
        if name not in agents:
            raise ValueError(f"Agent not found: {name}")
        queue = agents[name].get("queue", [])
        new_queue = [task for task in queue if task["task_id"] != task_id]
        if len(new_queue) != len(queue):
            agents[name]["queue"] = new_queue
            self._write(agents)
        return new_queue

    def reorder_queue(self, name: str, task_ids: list[str]) -> list[dict]:
        """Reorder queue by given task_id list. Raises ValueError on mismatch."""
        agents = self._read()
        if name not in agents:
            raise ValueError(f"Agent not found: {name}")
        queue = agents[name].get("queue", [])
        current_ids = [task["task_id"] for task in queue]
        if sorted(task_ids) != sorted(current_ids):
            raise ValueError(f"task_ids mismatch: expected {current_ids}, got {task_ids}")
        by_id = {task["task_id"]: task for task in queue}
        agents[name]["queue"] = [by_id[task_id] for task_id in task_ids]
        self._write(agents)
        return agents[name]["queue"]

    def update_capabilities(self, name: str, capabilities: list[str]) -> dict:
        """Set capabilities list. Returns updated agent info."""
        agents = self._read()
        if name not in agents:
            raise ValueError(f"Agent not found: {name}")
        agents[name]["capabilities"] = capabilities
        self._write(agents)
        return {"name": name, **agents[name]}

    def _read(self) -> dict[str, dict]:
        """Read agents.json, returning empty dict if missing or corrupt."""
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning("agents.json is not a dict, treating as empty")
                return {}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read agents.json: %s", exc)
            return {}

    def _write(self, agents: dict[str, dict]) -> None:
        """Atomic write: write to temp file then rename."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        content = json.dumps(agents, indent=2, ensure_ascii=False) + "\n"

        fd, tmp_path = tempfile.mkstemp(
            dir=self.data_dir,
            prefix=".agents-",
            suffix=".tmp",
        )
        try:
            with open(fd, "w", encoding="utf-8") as file_obj:
                file_obj.write(content)
            Path(tmp_path).replace(self._path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
