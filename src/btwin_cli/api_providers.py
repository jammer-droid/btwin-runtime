"""Provider status API routes."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter

from btwin_core.providers import (
    get_provider_status_public_id,
    get_provider_status_supported_transport_modes,
)

if TYPE_CHECKING:
    from btwin_core.runtime_logging import RuntimeEventLogger

_PROVIDER_TARGETS: tuple[str, ...] = ("claude-code", "codex")
_PROVIDER_CLI_NAMES: dict[str, str] = {
    "claude-code": "claude",
    "codex": "codex",
}
_PROVIDER_AUTH_ENV_VARS: dict[str, tuple[str, ...]] = {
    "claude-code": ("ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY"),
    "codex": ("OPENAI_API_KEY",),
}
_CACHE_TTL_SECONDS = 60.0
_CACHE: dict[str, float | list[dict[str, object]] | None] = {
    "checked_at": 0.0,
    "payload": None,
}


def create_providers_router(*, runtime_event_logger: RuntimeEventLogger | None = None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/providers/status")
    def get_provider_status():
        rows = _get_provider_status_rows()
        if runtime_event_logger is not None:
            runtime_event_logger.log(
                "provider_status_checked",
                level="info",
                message="provider status checked",
                details={"providers": [row["id"] for row in rows]},
            )
        return {"providers": rows}

    return router


def _get_provider_status_rows() -> list[dict[str, object]]:
    now = time.monotonic()
    cached_at = cast(float, _CACHE["checked_at"])
    cached_payload = cast(list[dict[str, object]] | None, _CACHE["payload"])
    if cached_payload is not None and now - cached_at < _CACHE_TTL_SECONDS:
        return cached_payload

    checked_at = datetime.now(timezone.utc).isoformat()
    rows = [
        _build_provider_status_row(provider_name, checked_at=checked_at)
        for provider_name in _PROVIDER_TARGETS
    ]
    _CACHE["checked_at"] = now
    _CACHE["payload"] = rows
    return rows


def _build_provider_status_row(provider_name: str, *, checked_at: str) -> dict[str, object]:
    cli_name = _PROVIDER_CLI_NAMES.get(provider_name, provider_name)
    detected = _detect_cli(cli_name)
    authenticated = detected and _detect_authenticated(provider_name)
    row: dict[str, object] = {
        "id": get_provider_status_public_id(provider_name),
        "detected": detected,
        "authenticated": authenticated,
        "supported_transport_modes": list(
            get_provider_status_supported_transport_modes(provider_name)
        ),
        "last_checked_at": checked_at,
    }
    version = _lookup_cli_version(cli_name) if detected else None
    if version:
        row["version"] = version
    return row


def _detect_cli(cli_name: str) -> bool:
    return shutil.which(cli_name) is not None


def _lookup_cli_version(cli_name: str) -> str | None:
    try:
        result = subprocess.run(
            [cli_name, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return None
    return output.splitlines()[0].strip()


def _detect_authenticated(provider_name: str) -> bool:
    for env_var in _PROVIDER_AUTH_ENV_VARS.get(provider_name, ()):  # pragma: no branch - tiny loop
        if os.environ.get(env_var):
            return True
    return False
