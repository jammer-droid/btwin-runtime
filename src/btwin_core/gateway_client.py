"""Gateway client contracts for runtime launch decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from btwin_core.config import BTwinConfig


@dataclass(frozen=True, slots=True)
class GatewayLaunchContext:
    provider_name: str
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GatewayLaunchDecision:
    mode: str
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


class GatewayClient(Protocol):
    mode: str

    def prepare_launch(self, context: GatewayLaunchContext) -> GatewayLaunchDecision: ...


@dataclass(frozen=True, slots=True)
class PassthroughGatewayClient:
    mode: str = "passthrough"

    def prepare_launch(self, context: GatewayLaunchContext) -> GatewayLaunchDecision:
        route = _resolve_gateway_route(context)
        metadata = {
            **context.metadata,
            "gateway_mode": self.mode,
        }
        metadata.setdefault("gateway_route", route)
        return GatewayLaunchDecision(
            mode=self.mode,
            command=list(context.command),
            env=dict(context.env),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class InternalGatewayClient:
    base_url: str | None = None
    mode: str = "internal"

    def prepare_launch(self, context: GatewayLaunchContext) -> GatewayLaunchDecision:
        route = _resolve_gateway_route(context)
        metadata = {
            **context.metadata,
            "gateway_mode": self.mode,
        }
        metadata.setdefault("gateway_route", route)
        if self.base_url:
            metadata["gateway_base_url"] = self.base_url
        env = dict(context.env)
        env["BTWIN_GATEWAY_MODE"] = self.mode
        env["BTWIN_GATEWAY_ROUTE"] = route
        if self.base_url:
            env["BTWIN_GATEWAY_BASE_URL"] = self.base_url
        return GatewayLaunchDecision(
            mode=self.mode,
            command=list(context.command),
            env=env,
            metadata=metadata,
        )


def build_gateway_client(config: BTwinConfig) -> GatewayClient:
    runtime = config.runtime
    if runtime.gateway_internal_enabled:
        return InternalGatewayClient(base_url=runtime.gateway_base_url)
    return PassthroughGatewayClient()


def _resolve_gateway_route(context: GatewayLaunchContext) -> str:
    route = context.metadata.get("gateway_route")
    if isinstance(route, str) and route:
        return route
    return context.provider_name
