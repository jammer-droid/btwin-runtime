"""Configuration management for B-TWIN."""

import os
from pathlib import Path

import yaml
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def resolve_data_dir() -> Path:
    """Resolve data directory with precedence: env var > project-local > global default."""
    # 1. Environment variable (highest priority)
    env_dir = os.environ.get("BTWIN_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser()

    # 2. Per-project .btwin/ directory
    project_dir = Path.cwd() / ".btwin"
    if project_dir.is_dir():
        return project_dir

    # 3. Global default
    return Path.home() / ".btwin"


def resolve_config_path() -> Path:
    """Resolve config path with precedence: env var > global default."""
    env_path = os.environ.get("BTWIN_CONFIG_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".btwin" / "config.yaml"


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    api_key: str | None = None


class SessionConfig(BaseModel):
    timeout_minutes: int = 10


class PromotionConfig(BaseModel):
    enabled: bool = True
    schedule: str = "0 9,21 * * *"


class RuntimeConfig(BaseModel):
    mode: Literal["attached", "standalone"] = "attached"
    openclaw_config_path: Path | None = None
    gateway_enabled: bool = False
    gateway_base_url: str | None = None
    gateway_mode: Literal["passthrough", "internal"] = "passthrough"
    persistent_transport_enabled: bool = False
    persistent_transport_providers: list[str] = Field(default_factory=list)
    persistent_transport_auto_fallback: bool = True

    @field_validator("gateway_mode", mode="before")
    @classmethod
    def normalize_gateway_mode(cls, value: object) -> object:
        if value == "disabled":
            return "passthrough"
        return value

    @property
    def gateway_internal_enabled(self) -> bool:
        return self.gateway_enabled and self.gateway_mode == "internal"


class ConsolidationConfig(BaseModel):
    enabled: bool = True
    auto_threshold: float = 0.95
    suggest_threshold: float = 0.80
    search_candidates: int = 3


class ConsolidationConfig(BaseModel):
    enabled: bool = True
    auto_threshold: float = 0.95
    suggest_threshold: float = 0.80
    search_candidates: int = 3


class BTwinConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    promotion: PromotionConfig = Field(default_factory=PromotionConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    consolidation: ConsolidationConfig = Field(default_factory=ConsolidationConfig)
    data_dir: Path = Field(default_factory=resolve_data_dir)


def _is_valid_local_path(p: str | Path) -> bool:
    """Check if a path string is valid for the current OS."""
    s = str(p)
    if os.name != "nt" and (s.startswith("C:") or "\\" in s):
        return False
    if os.name == "nt" and s.startswith("/") and not s.startswith("//"):
        return False
    return True


def load_config(path: Path) -> BTwinConfig:
    """Load config from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    # Drop data_dir if it belongs to a different OS
    if "data_dir" in data and not _is_valid_local_path(data["data_dir"]):
        del data["data_dir"]
    return BTwinConfig(**data)
