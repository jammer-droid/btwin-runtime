"""Auth adapter contracts for provider launch preparation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from btwin_core.config import RuntimeConfig
from btwin_core.providers import get_provider_runtime_profile


@dataclass(frozen=True, slots=True)
class ResolvedLaunchAuth:
    provider_name: str
    mode: str
    env: dict[str, str] = field(default_factory=dict)
    token_ref: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class AuthAdapter(Protocol):
    provider_name: str

    def resolve(
        self,
        *,
        agent: Mapping[str, Any] | None,
        runtime_config: RuntimeConfig,
    ) -> ResolvedLaunchAuth: ...

    def resolve_launch_auth(self) -> ResolvedLaunchAuth: ...


def _cli_auth_hints(agent: Mapping[str, Any] | None) -> dict[str, Any]:
    if agent is None:
        return {}
    cli_config = agent.get("cli_config")
    if not isinstance(cli_config, Mapping):
        return {}
    auth = cli_config.get("auth")
    return dict(auth) if isinstance(auth, Mapping) else {}


def _build_resolved_auth(
    *,
    provider_name: str,
    mode: str,
    env: Mapping[str, str] | None = None,
    token_ref: str | None = None,
) -> ResolvedLaunchAuth:
    return ResolvedLaunchAuth(
        provider_name=provider_name,
        mode=mode,
        env=dict(env or {}),
        token_ref=token_ref,
        metadata={"auth_mode": mode},
    )


@dataclass(frozen=True, slots=True)
class ClaudeAuthAdapter:
    provider_name: str = "claude-code"
    auth_mode: str = "cli_environment"

    def resolve(
        self,
        *,
        agent: Mapping[str, Any] | None,
        runtime_config: RuntimeConfig,
    ) -> ResolvedLaunchAuth:
        del agent
        del runtime_config
        return _build_resolved_auth(
            provider_name=self.provider_name,
            mode=self.auth_mode,
        )

    def resolve_launch_auth(self) -> ResolvedLaunchAuth:
        return _build_resolved_auth(
            provider_name=self.provider_name,
            mode=self.auth_mode,
        )


@dataclass(frozen=True, slots=True)
class CodexAuthAdapter:
    provider_name: str = "codex"
    auth_mode: str = "cli_environment"

    def resolve(
        self,
        *,
        agent: Mapping[str, Any] | None,
        runtime_config: RuntimeConfig,
    ) -> ResolvedLaunchAuth:
        del runtime_config
        auth_hints = _cli_auth_hints(agent)
        mode = auth_hints.get("mode")
        token_ref = auth_hints.get("token_ref")
        if isinstance(mode, str) and mode.strip():
            resolved_mode = mode.strip()
            resolved_token_ref = (
                token_ref.strip()
                if resolved_mode == "stored_token" and isinstance(token_ref, str) and token_ref.strip()
                else None
            )
            return _build_resolved_auth(
                provider_name=self.provider_name,
                mode=resolved_mode,
                token_ref=resolved_token_ref,
            )
        if isinstance(token_ref, str) and token_ref.strip():
            return _build_resolved_auth(
                provider_name=self.provider_name,
                mode="stored_token",
                token_ref=token_ref.strip(),
            )
        return _build_resolved_auth(
            provider_name=self.provider_name,
            mode=self.auth_mode,
        )

    def resolve_launch_auth(self) -> ResolvedLaunchAuth:
        return _build_resolved_auth(
            provider_name=self.provider_name,
            mode=self.auth_mode,
        )


def build_auth_adapter(provider_name: str) -> AuthAdapter:
    profile = get_provider_runtime_profile(provider_name)
    canonical_name = profile.canonical_name if profile is not None else provider_name
    auth_mode = profile.default_auth_mode if profile is not None else "cli_environment"

    if canonical_name == "claude-code":
        return ClaudeAuthAdapter(auth_mode=auth_mode)
    if canonical_name == "codex":
        return CodexAuthAdapter(auth_mode=auth_mode)
    raise ValueError(f"Unsupported provider for auth adapter: {provider_name}")
