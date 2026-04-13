"""Protocol store — loads and manages collaboration protocol definitions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


class ProtocolSection(BaseModel):
    section: str
    required: bool = False
    guidance: str = ""


class CycleConfig(BaseModel):
    until: Literal["decide"] = "decide"


class ProtocolPhase(BaseModel):
    name: str
    description: str = ""
    actions: list[Literal["contribute", "review", "discuss", "decide"]] = []
    template: list[ProtocolSection] | None = None
    mode: Literal["realtime_messages"] | None = None
    guidance: str | None = None
    decided_by: Literal["user", "consensus", "vote"] | None = None
    cycle: CycleConfig | None = None

    @model_validator(mode="after")
    def normalize_actions(self) -> "ProtocolPhase":
        """Migrate legacy mode to actions and apply defaults."""
        if not self.actions:
            inferred = []
            if self.mode == "realtime_messages":
                inferred.append("discuss")
            if self.template:
                inferred.append("contribute")
            if self.decided_by:
                inferred.append("decide")
            if not inferred:
                inferred.append("discuss")
            self.actions = inferred
        if self.decided_by and "decide" not in self.actions:
            self.actions.append("decide")
        return self


class ProtocolTransition(BaseModel):
    from_phase: str = Field(alias="from")
    to: str
    on: str | None = None


class Protocol(BaseModel):
    name: str
    description: str = ""
    phases: list[ProtocolPhase]
    roles: list[str] = []
    transitions: list[ProtocolTransition] = []
    outcomes: list[str] = []


class ProtocolStore:
    """Read-only store for protocol YAML definitions."""

    def __init__(self, protocols_dir: Path, fallback_dir: Path | None = None) -> None:
        self._dir = protocols_dir
        self._fallback_dir = fallback_dir

    def list_protocols(self) -> list[dict]:
        """Return summary of all valid protocols."""
        results = {}
        for base_dir in self._candidate_dirs():
            if not base_dir.exists():
                continue
            for path in sorted(base_dir.glob("*.yaml")):
                proto = self._load_file(path)
                if proto and proto.name not in results:
                    results[proto.name] = {
                        "name": proto.name,
                        "description": proto.description,
                    }
        return list(results.values())

    def get_protocol(self, name: str) -> Protocol | None:
        """Load full protocol definition by name."""
        for base_dir in self._candidate_dirs():
            path = base_dir / f"{name}.yaml"
            if path.exists():
                return self._load_file(path)
        return None

    def save_protocol(self, protocol: Protocol) -> Path:
        """Save protocol to project-local directory."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{protocol.name}.yaml"
        data = protocol.model_dump(exclude_none=True)
        for phase in data.get("phases", []):
            phase.pop("mode", None)
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def delete_protocol(self, name: str) -> bool:
        """Delete project-local protocol. Returns False if not found."""
        path = self._dir / f"{name}.yaml"
        if path.exists():
            path.unlink()
            return True
        return False

    def _candidate_dirs(self) -> list[Path]:
        dirs = [self._dir]
        if self._fallback_dir is not None:
            dirs.append(self._fallback_dir)
        return dirs

    def _load_file(self, path: Path) -> Protocol | None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            return Protocol.model_validate(data)
        except (OSError, yaml.YAMLError, ValidationError):
            logger.warning("Failed to load protocol: %s", path)
            return None
