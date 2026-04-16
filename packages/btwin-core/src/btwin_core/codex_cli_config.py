"""Helpers for building Codex CLI config override arguments."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def build_codex_config_args(config_overrides: Mapping[str, Any] | None) -> list[str]:
    if not config_overrides:
        return []

    args: list[str] = []
    for key, value in config_overrides.items():
        if not isinstance(key, str) or not key.strip() or value is None:
            continue
        args.extend(["-c", f"{key}={_format_config_value(value)}"])
    return args


def _format_config_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)
