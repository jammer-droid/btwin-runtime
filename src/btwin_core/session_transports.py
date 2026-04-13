"""Production transport contracts for runtime-owned agent sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from btwin_core.config import RuntimeConfig
from btwin_core.providers import get_provider_runtime_profile
from btwin_core.prototypes.persistent_sessions.base import PersistentSessionAdapter
from btwin_core.prototypes.persistent_sessions.claude_adapter import ClaudePersistentAdapter
from btwin_core.prototypes.persistent_sessions.codex_app_server_adapter import (
    CodexAppServerPersistentAdapter,
)
from btwin_core.prototypes.persistent_sessions.types import SessionConfig


@dataclass(frozen=True)
class TransportLaunchContext:
    provider_name: str
    transport_mode: str
    auth_mode: str | None = None
    token_ref: str | None = None
    gateway_metadata: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)

    def build_session_config(self, *, resume_session_id: str | None = None) -> SessionConfig:
        metadata = dict(self.gateway_metadata)
        if self.auth_mode:
            metadata["auth_mode"] = self.auth_mode
        if self.token_ref:
            metadata["token_ref"] = self.token_ref
        if resume_session_id:
            metadata["resume_session_id"] = resume_session_id
        return SessionConfig(
            options={"env": dict(self.env)} if self.env else {},
            metadata=metadata,
        )


class RuntimeTransport(Protocol):
    provider_name: str
    mode: str
    fallback_mode: str | None
    requires_health_check_before_reuse: bool
    supports_resume_fallback: bool

    def build_adapter(
        self,
        launch_context: TransportLaunchContext | None = None,
    ) -> PersistentSessionAdapter | None:
        """Return the prototype-backed adapter that implements this transport."""

    def build_session_config(
        self,
        launch_context: TransportLaunchContext | None = None,
        *,
        resume_session_id: str | None = None,
    ) -> SessionConfig | None:
        """Build a transport-owned session config when the transport needs one."""


@dataclass(frozen=True)
class LiveProcessTransport:
    provider_name: str
    adapter_key: str
    fallback_mode: str | None = None
    requires_health_check_before_reuse: bool = True
    supports_resume_fallback: bool = True
    mode: str = "live_process_transport"

    def build_adapter(
        self,
        launch_context: TransportLaunchContext | None = None,
    ) -> PersistentSessionAdapter:
        del launch_context
        if self.adapter_key == "claude":
            return ClaudePersistentAdapter()
        if self.adapter_key == "codex-app-server":
            return CodexAppServerPersistentAdapter()
        raise ValueError(f"unsupported live adapter key: {self.adapter_key}")

    def build_session_config(
        self,
        launch_context: TransportLaunchContext | None = None,
        *,
        resume_session_id: str | None = None,
    ) -> SessionConfig:
        if launch_context is None:
            return SessionConfig(
                options={},
                metadata={"resume_session_id": resume_session_id} if resume_session_id else {},
            )
        return launch_context.build_session_config(resume_session_id=resume_session_id)


@dataclass(frozen=True)
class ResumeInvocationTransport:
    provider_name: str
    fallback_mode: str | None = None
    requires_health_check_before_reuse: bool = False
    supports_resume_fallback: bool = False
    mode: str = "resume_invocation_transport"

    def build_adapter(
        self,
        launch_context: TransportLaunchContext | None = None,
    ) -> PersistentSessionAdapter | None:
        del launch_context
        return None

    def build_session_config(
        self,
        launch_context: TransportLaunchContext | None = None,
        *,
        resume_session_id: str | None = None,
    ) -> SessionConfig | None:
        del launch_context
        del resume_session_id
        return None


def build_transport_for_provider(
    provider_name: str,
    runtime_config: RuntimeConfig | None = None,
) -> RuntimeTransport:
    """Resolve the production transport contract for a provider or alias."""
    profile = get_provider_runtime_profile(provider_name)
    if profile is None:
        return ResumeInvocationTransport(provider_name=provider_name)

    if _persistent_transport_enabled_for_provider(
        profile=profile,
        provider_name=provider_name,
        runtime_config=runtime_config,
    ) and profile.live_adapter_key:
        fallback_mode = profile.fallback_mode if _auto_fallback_enabled(runtime_config) else None
        if fallback_mode is None and _auto_fallback_enabled(runtime_config):
            fallback_mode = "resume_invocation_transport"
        return LiveProcessTransport(
            provider_name=profile.canonical_name,
            adapter_key=profile.live_adapter_key,
            fallback_mode=fallback_mode,
            supports_resume_fallback=bool(fallback_mode),
        )
    return ResumeInvocationTransport(
        provider_name=profile.canonical_name,
    )


def _persistent_transport_enabled_for_provider(
    *,
    profile,
    provider_name: str,
    runtime_config: RuntimeConfig | None,
) -> bool:
    if runtime_config is None:
        return False
    if not runtime_config.persistent_transport_enabled:
        return False
    if not profile.supports_live_transport:
        return False

    allowed_providers = {
        str(value).strip()
        for value in runtime_config.persistent_transport_providers
        if isinstance(value, str) and value.strip()
    }
    if not allowed_providers:
        return False

    candidate_names = {provider_name, profile.canonical_name, *profile.aliases}
    return bool(candidate_names & allowed_providers)


def _auto_fallback_enabled(runtime_config: RuntimeConfig | None) -> bool:
    if runtime_config is None:
        return True
    return runtime_config.persistent_transport_auto_fallback
