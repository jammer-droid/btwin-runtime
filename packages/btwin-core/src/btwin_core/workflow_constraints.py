"""Workflow constraint evaluation and Codex hook output helpers."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from btwin_core.protocol_store import Protocol
from btwin_core.protocol_validator import ProtocolValidator


WorkflowHookEvent = Literal["SessionStart", "UserPromptSubmit", "Stop"]
WorkflowHookDecision = Literal["allow", "block", "noop"]


class WorkflowHookResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: WorkflowHookEvent
    decision: WorkflowHookDecision
    reason: str | None = None
    overlay: str | None = None
    required_result_recorded: bool = False


class CodexHookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str | None = None
    transcript_path: str | None = None
    cwd: str | None = None
    hook_event_name: str
    model: str | None = None
    turn_id: str | None = None
    source: str | None = None
    prompt: str | None = None
    stop_hook_active: bool | None = None
    last_assistant_message: str | None = None

    @classmethod
    def from_text(cls, text: str) -> "CodexHookPayload | None":
        stripped = text.strip()
        if not stripped:
            return None
        try:
            return cls.model_validate(json.loads(stripped))
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


def _required_sections(protocol: Protocol, phase_name: str | None) -> list[str]:
    if not phase_name:
        return []
    phase = next((item for item in protocol.phases if item.name == phase_name), None)
    if phase is None:
        return []
    return [section.section for section in phase.template if section.required]


def _actor_contribution_matches(
    *,
    actor: str | None,
    phase_name: str | None,
    required_sections: list[str],
    contributions: list[dict],
) -> bool:
    if not actor or not phase_name:
        return False

    for contribution in contributions:
        if contribution.get("agent") != actor:
            continue
        if contribution.get("phase") != phase_name:
            continue
        content = contribution.get("_content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        if not required_sections:
            return True
        validation = ProtocolValidator.validate_contribution(content, required_sections)
        if validation.valid:
            return True
    return False


def evaluate_workflow_hook(
    *,
    event: WorkflowHookEvent,
    thread: dict,
    protocol: Protocol,
    actor: str | None,
    contributions: list[dict],
) -> WorkflowHookResult:
    """Evaluate the minimal workflow constraint contract for one hook event."""
    current_phase = thread.get("current_phase")
    phase_name = current_phase if isinstance(current_phase, str) else None
    required_sections = _required_sections(protocol, phase_name)

    if event == "SessionStart":
        return WorkflowHookResult(
            event=event,
            decision="noop",
            overlay=f"Resume thread {thread.get('thread_id')} in phase {phase_name or 'unknown'}.",
        )

    if event == "UserPromptSubmit":
        return WorkflowHookResult(
            event=event,
            decision="noop",
            overlay=f"Current phase: {phase_name or 'unknown'}. Required result type: contribution.",
        )

    required_result_recorded = _actor_contribution_matches(
        actor=actor,
        phase_name=phase_name,
        required_sections=required_sections,
        contributions=contributions,
    )
    if required_result_recorded:
        return WorkflowHookResult(
            event=event,
            decision="allow",
            required_result_recorded=True,
        )

    return WorkflowHookResult(
        event=event,
        decision="block",
        reason="missing_contribution",
        overlay=(
            f"Current phase {phase_name or 'unknown'} still needs a contribution "
            f"from {actor or 'the current actor'} before stopping."
        ),
        required_result_recorded=False,
    )


def build_codex_hook_response(
    payload: CodexHookPayload,
    result: WorkflowHookResult,
) -> dict[str, object] | None:
    """Render a WorkflowHookResult into the Codex hook stdout JSON shape."""
    if payload.hook_event_name == "UserPromptSubmit":
        return None

    if payload.hook_event_name == "SessionStart":
        if not result.overlay:
            return None
        return {
            "hookSpecificOutput": {
                "hookEventName": payload.hook_event_name,
                "additionalContext": result.overlay,
            }
        }

    if payload.hook_event_name == "Stop" and result.decision == "block":
        return {
            "decision": "block",
            "reason": result.overlay or result.reason or "Continue the current phase before stopping.",
        }

    return None
