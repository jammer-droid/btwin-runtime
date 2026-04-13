from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class MiniRouteDecision:
    mode: str
    targets: list[str]
    confidence: float
    reason: str


class MiniRouterJudge:
    def __init__(
        self,
        *,
        decider: Callable[[str], dict | None] | None = None,
        feature_enabled: bool | None = None,
        confidence_threshold: float = 0.7,
    ) -> None:
        self._decider = decider
        self._feature_enabled = feature_enabled
        self._confidence_threshold = confidence_threshold

    @property
    def enabled(self) -> bool:
        if self._feature_enabled is not None:
            return self._feature_enabled
        return os.getenv("BTWIN_ENABLE_MINI_ROUTER", "").lower() in {"1", "true", "yes", "on"}

    def decide(self, *, snapshot_text: str) -> MiniRouteDecision | None:
        if not self.enabled or self._decider is None:
            return None
        return self._normalize(self._decider(snapshot_text))

    def _normalize(self, raw: dict | None) -> MiniRouteDecision | None:
        if not isinstance(raw, dict):
            return None

        mode = str(raw.get("mode", "")).strip()
        if mode not in {"ignore", "direct", "multicast", "broadcast"}:
            return None

        targets = raw.get("targets") or []
        if not isinstance(targets, list):
            return None
        normalized_targets = [str(target).strip() for target in targets if str(target).strip()]

        if mode in {"direct", "multicast"} and not normalized_targets:
            return None

        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None

        if confidence < self._confidence_threshold or confidence > 1.0:
            return None

        reason = str(raw.get("reason", "")).strip()
        return MiniRouteDecision(
            mode=mode,
            targets=normalized_targets,
            confidence=confidence,
            reason=reason,
        )
