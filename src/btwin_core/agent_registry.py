"""Agent registry loader for orchestration gate authorization."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


DEFAULT_OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"


def resolve_openclaw_config_path(override_path: str | None = None) -> Path:
    if override_path:
        return Path(override_path).expanduser()

    env_path = os.environ.get("BTWIN_OPENCLAW_CONFIG_PATH")
    if env_path:
        return Path(env_path).expanduser()

    return DEFAULT_OPENCLAW_CONFIG


class AgentRegistry:
    def __init__(
        self,
        config_path: Path | None = None,
        extra_agents: set[str] | None = None,
        initial_agents: set[str] | None = None,
    ) -> None:
        self.config_path = config_path
        self.extra_agents = set(extra_agents or set())
        self.initial_agents = set(initial_agents or set())
        self._agents: set[str] = set(self.initial_agents)

        if not initial_agents:
            self.reload()

    @property
    def agents(self) -> set[str]:
        return set(self._agents)

    def is_allowed(self, agent: str) -> bool:
        return agent in self._agents

    def reload(self, override_path: str | None = None) -> dict[str, object]:
        path = self.config_path or resolve_openclaw_config_path(override_path)
        loaded_agents = set(self.initial_agents)
        loaded_agents.update(self.extra_agents)

        if path.exists():
            try:
                data = json.loads(path.read_text())
                configured = data.get("agents", {})
                if isinstance(configured, dict):
                    loaded_agents.update(configured.keys())
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load agent config from %s: %s", path, exc)

        self._agents = loaded_agents
        return {
            "path": str(path),
            "count": len(self._agents),
            "agents": sorted(self._agents),
        }
