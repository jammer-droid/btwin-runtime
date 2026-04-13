"""Shared helper functions for the B-TWIN API."""

from __future__ import annotations

import hmac
from uuid import uuid4

from fastapi.responses import JSONResponse

from btwin_core.storage import Storage


def trace_id() -> str:
    return f"trc_{uuid4().hex[:12]}"


def error_response(
    status_code: int,
    error_code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "errorCode": error_code,
            "message": message,
            "details": details or {},
            "traceId": trace_id(),
        },
    )


def require_admin_token(
    token: str | None,
    expected_token: str | None,
) -> JSONResponse | None:
    if not expected_token:
        return None
    if token and hmac.compare_digest(token, expected_token):
        return None
    return error_response(403, "FORBIDDEN", "admin token is required")


def require_main_admin(
    actor: str,
    token: str | None,
    expected_token: str | None,
    *,
    registry_agents: set[str],
) -> JSONResponse | None:
    from btwin_core.workflow_gate import validate_actor, validate_promotion_approval

    actor_decision = validate_actor(actor, registry_agents)
    if not actor_decision.ok:
        return error_response(403, "FORBIDDEN", actor_decision.message or "forbidden", actor_decision.details)

    approval_decision = validate_promotion_approval(actor)
    if not approval_decision.ok:
        return error_response(403, "FORBIDDEN", approval_decision.message or "forbidden", approval_decision.details)

    return require_admin_token(token, expected_token)


def split_markdown_document(raw: str) -> tuple[dict[str, object], str]:
    metadata = Storage._parse_frontmatter_metadata(raw) or {}
    if raw.startswith("---\n"):
        parts = raw.split("---\n", 2)
        if len(parts) >= 3:
            return metadata, parts[2].lstrip("\n")
    return metadata, raw


def collapse_ws(text: str) -> str:
    return " ".join(text.replace("\r", "\n").split())


def truncate(text: str, limit: int = 220) -> str:
    compact = collapse_ws(text)
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "\u2026"
