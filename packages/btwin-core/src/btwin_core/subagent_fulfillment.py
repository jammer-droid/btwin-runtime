"""Models for protocol role fulfillment and Codex sub-agent profiles."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


FulfillmentMode = Literal["registered_agent", "foreground_subagent", "managed_agent_subagent"]


class SubagentToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class SubagentContextPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include: list[str] = Field(default_factory=list)


class SubagentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    model: str | None = None
    reasoning_effort: str | None = None
    persona: str = ""
    tools: SubagentToolPolicy = Field(default_factory=SubagentToolPolicy)
    context: SubagentContextPolicy = Field(default_factory=SubagentContextPolicy)


class RoleFulfillment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: FulfillmentMode = "registered_agent"
    parent: str | None = None
    profile: str | None = None
    subagent_type: str | None = None
    agent: str | None = None
