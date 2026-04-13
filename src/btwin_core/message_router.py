from __future__ import annotations

from dataclasses import dataclass

from btwin_core.context_formatter import ContextFormatter
from btwin_core.mini_router import MiniRouterJudge, MiniRouteDecision


@dataclass(frozen=True)
class RouteDecision:
    mode: str
    targets: list[str]
    source: str
    reason: str
    confidence: float = 1.0


class MessageRouter:
    def __init__(self, mini_router: MiniRouterJudge | None = None) -> None:
        self._mini_router = mini_router or MiniRouterJudge()

    def route(
        self,
        *,
        thread: dict,
        envelope: dict,
        managed_agents: set[str],
        snapshot: dict | None = None,
    ) -> RouteDecision:
        if thread.get("status") != "active":
            return RouteDecision("ignore", [], "deterministic", "thread_inactive", 1.0)

        from_agent = str(envelope.get("from_agent", ""))
        if from_agent in managed_agents:
            return RouteDecision("ignore", [], "deterministic", "managed_agent_message", 1.0)

        available_targets = sorted(agent for agent in managed_agents if agent != from_agent)
        if not available_targets:
            return RouteDecision("ignore", [], "fallback", "no_managed_targets", 1.0)

        delivery_mode = str(envelope.get("delivery_mode", "auto") or "auto")
        requested_targets = list(envelope.get("target_agents") or [])
        valid_targets = [agent for agent in requested_targets if agent in available_targets]

        if delivery_mode == "direct":
            if not valid_targets:
                return RouteDecision("ignore", [], "deterministic", "no_valid_direct_targets", 1.0)
            mode = "direct" if len(valid_targets) == 1 else "multicast"
            return RouteDecision(mode, valid_targets, "deterministic", "explicit_target_selection", 1.0)

        if delivery_mode == "broadcast":
            return RouteDecision("broadcast", available_targets, "deterministic", "explicit_broadcast", 1.0)

        if len(available_targets) == 1:
            return RouteDecision("direct", available_targets, "fallback", "single_managed_target", 1.0)

        if snapshot is not None:
            mini_decision = self._decide_with_mini_router(
                snapshot=snapshot,
                content=str(envelope.get("content", "")),
                available_targets=available_targets,
            )
            if mini_decision is not None:
                return mini_decision

        return RouteDecision("broadcast", available_targets, "fallback", "multi_target_auto_fallback", 1.0)

    def _decide_with_mini_router(
        self,
        *,
        snapshot: dict,
        content: str,
        available_targets: list[str],
    ) -> RouteDecision | None:
        mini_decision = self._mini_router.decide(
            snapshot_text=ContextFormatter.render_routing_snapshot(snapshot, content),
        )
        if mini_decision is None:
            return None

        valid_targets = [target for target in mini_decision.targets if target in available_targets]
        if mini_decision.mode in {"direct", "multicast"} and not valid_targets:
            return None

        if mini_decision.mode == "ignore":
            return RouteDecision("ignore", [], "mini_model", mini_decision.reason, mini_decision.confidence)
        if mini_decision.mode == "broadcast":
            return RouteDecision("broadcast", available_targets, "mini_model", mini_decision.reason, mini_decision.confidence)
        return RouteDecision(
            mini_decision.mode,
            valid_targets,
            "mini_model",
            mini_decision.reason,
            mini_decision.confidence,
        )
