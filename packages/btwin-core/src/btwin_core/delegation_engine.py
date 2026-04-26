"""Normalize compiled protocol and phase-cycle state into delegation decisions."""

from __future__ import annotations

import shlex
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from btwin_core.delegation_state import DelegationStatus
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_engine import build_phase_cycle_context_core, resolve_phase_cycle_current_step
from btwin_core.protocol_flow import ProtocolNextPlan, describe_next, resolve_protocol_phase
from btwin_core.protocol_store import Protocol, ensure_protocol_compiled
from btwin_core.subagent_fulfillment import RoleFulfillment, SubagentProfile

DEFAULT_MAX_AUTO_ITERATIONS = 5


class DelegationAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DelegationStatus
    next_phase: str | None = None
    target_role: str | None = None
    resolved_agent: str | None = None
    required_action: str | None = None
    expected_output: str | None = None
    fulfillment_mode: str = "registered_agent"
    parent_executor: str | None = None
    subagent_profile: str | None = None
    subagent_type: str | None = None
    executor_id: str | None = None
    reason_blocked: str | None = None
    stop_reason: str | None = None


def build_delegation_assignment(
    *,
    thread: dict[str, object],
    protocol: Protocol,
    phase_cycle_state: PhaseCycleState,
    role_bindings: dict[str, str] | None = None,
    contributions: list[dict[str, object]] | None = None,
    runtime_session: Mapping[str, object] | None = None,
    loop_iteration: int | None = None,
    max_auto_iterations: int = DEFAULT_MAX_AUTO_ITERATIONS,
) -> DelegationAssignment:
    compiled_protocol = ensure_protocol_compiled(protocol)
    current_phase = _current_phase_name(thread, phase_cycle_state)
    phase = resolve_protocol_phase(compiled_protocol, current_phase)
    if phase is None:
        return DelegationAssignment(
            status="blocked",
            required_action="record_outcome",
            reason_blocked="phase_not_found",
            stop_reason="phase_not_found",
        )

    thread_snapshot = dict(thread)
    thread_snapshot["current_phase"] = phase.name

    if phase_cycle_state.status == "blocked":
        return DelegationAssignment(
            status="blocked",
            required_action="submit_contribution",
            expected_output=_fallback_expected_output(phase.name),
            reason_blocked="phase_cycle_blocked",
            stop_reason="phase_cycle_blocked",
        )

    next_plan = describe_next(
        thread_snapshot,
        compiled_protocol,
        list(contributions or []),
    )
    if next_plan.manual_outcome_required:
        return DelegationAssignment(
            status="waiting_for_human",
            next_phase=next_plan.next_phase,
            required_action=next_plan.suggested_action,
            expected_output=_manual_outcome_output(phase, next_plan),
            stop_reason="human_outcome_required",
        )
    if phase_cycle_state.status == "completed" or next_plan.suggested_action in {"advance_phase", "close_thread"}:
        return DelegationAssignment(
            status="completed",
            next_phase=next_plan.next_phase,
            required_action=next_plan.suggested_action,
            stop_reason=next_plan.suggested_action,
        )

    current_step = resolve_phase_cycle_current_step(phase, phase_cycle_state)
    target_role = current_step.role if current_step is not None else None
    context_core = build_phase_cycle_context_core(
        thread=thread_snapshot,
        protocol=compiled_protocol,
        phase=phase,
        state=phase_cycle_state,
    )
    expected_output = _expected_output(
        phase_name=phase.name,
        next_plan=next_plan,
        step_action=current_step.action if current_step is not None else None,
        fallback=context_core.required_result,
    )
    if not target_role:
        return DelegationAssignment(
            status="blocked",
            next_phase=next_plan.next_phase,
            required_action=next_plan.suggested_action,
            expected_output=expected_output,
            reason_blocked="missing_target_role",
            stop_reason="missing_target_role",
        )

    fulfillment = _role_fulfillment(compiled_protocol, target_role)
    if fulfillment.mode == "foreground_subagent":
        return DelegationAssignment(
            status="blocked",
            next_phase=next_plan.next_phase,
            target_role=target_role,
            required_action=next_plan.suggested_action,
            expected_output=expected_output,
            fulfillment_mode=fulfillment.mode,
            parent_executor=fulfillment.parent or "foreground",
            subagent_profile=fulfillment.profile,
            subagent_type=fulfillment.subagent_type,
            reason_blocked="foreground_subagent_requires_managed_parent",
            stop_reason="foreground_subagent_requires_managed_parent",
        )
    if fulfillment.mode == "managed_agent_subagent":
        profile_name = fulfillment.profile
        if not profile_name or profile_name not in compiled_protocol.subagent_profiles:
            return DelegationAssignment(
                status="blocked",
                next_phase=next_plan.next_phase,
                target_role=target_role,
                required_action=next_plan.suggested_action,
                expected_output=expected_output,
                fulfillment_mode=fulfillment.mode,
                parent_executor=fulfillment.parent,
                subagent_profile=profile_name,
                subagent_type=fulfillment.subagent_type,
                reason_blocked="missing_subagent_profile",
                stop_reason="missing_subagent_profile",
            )
        parent_executor = fulfillment.parent or fulfillment.agent or (role_bindings or {}).get(target_role)
        if not parent_executor:
            return DelegationAssignment(
                status="blocked",
                next_phase=next_plan.next_phase,
                target_role=target_role,
                required_action=next_plan.suggested_action,
                expected_output=expected_output,
                fulfillment_mode=fulfillment.mode,
                subagent_profile=profile_name,
                subagent_type=fulfillment.subagent_type,
                reason_blocked="missing_parent_executor",
                stop_reason="missing_parent_executor",
            )
        if _runtime_recovery_failed(runtime_session):
            return DelegationAssignment(
                status="blocked",
                next_phase=next_plan.next_phase,
                target_role=target_role,
                resolved_agent=parent_executor,
                required_action=next_plan.suggested_action,
                expected_output=expected_output,
                fulfillment_mode=fulfillment.mode,
                parent_executor=parent_executor,
                subagent_profile=profile_name,
                subagent_type=fulfillment.subagent_type,
                reason_blocked="failed_recovery",
                stop_reason="failed_recovery",
            )
        if _loop_iteration_limit_reached(
            loop_iteration=loop_iteration,
            max_auto_iterations=max_auto_iterations,
        ):
            return DelegationAssignment(
                status="failed",
                next_phase=next_plan.next_phase,
                target_role=target_role,
                resolved_agent=parent_executor,
                required_action=next_plan.suggested_action,
                expected_output=expected_output,
                fulfillment_mode=fulfillment.mode,
                parent_executor=parent_executor,
                subagent_profile=profile_name,
                subagent_type=fulfillment.subagent_type,
                stop_reason="max_auto_iterations_reached",
            )
        return DelegationAssignment(
            status="running",
            next_phase=next_plan.next_phase,
            target_role=target_role,
            resolved_agent=parent_executor,
            required_action=next_plan.suggested_action,
            expected_output=expected_output,
            fulfillment_mode=fulfillment.mode,
            parent_executor=parent_executor,
            subagent_profile=profile_name,
            subagent_type=fulfillment.subagent_type,
            executor_id=_dispatch_executor_id(
                thread_id=str(thread.get("thread_id") or ""),
                phase_name=phase_cycle_state.phase_name,
                cycle_index=phase_cycle_state.cycle_index,
                role=target_role,
                profile=profile_name,
            ),
        )

    resolved_agent = fulfillment.agent or (role_bindings or {}).get(target_role)
    if not resolved_agent:
        return DelegationAssignment(
            status="blocked",
            next_phase=next_plan.next_phase,
            target_role=target_role,
            required_action=next_plan.suggested_action,
            expected_output=expected_output,
            fulfillment_mode=fulfillment.mode,
            reason_blocked="missing_role_binding",
            stop_reason="missing_role_binding",
        )
    if _runtime_recovery_failed(runtime_session):
        return DelegationAssignment(
            status="blocked",
            next_phase=next_plan.next_phase,
            target_role=target_role,
            resolved_agent=resolved_agent,
            required_action=next_plan.suggested_action,
            expected_output=expected_output,
            fulfillment_mode=fulfillment.mode,
            reason_blocked="failed_recovery",
            stop_reason="failed_recovery",
        )
    if _loop_iteration_limit_reached(
        loop_iteration=loop_iteration,
        max_auto_iterations=max_auto_iterations,
    ):
        return DelegationAssignment(
            status="failed",
            next_phase=next_plan.next_phase,
            target_role=target_role,
            resolved_agent=resolved_agent,
            required_action=next_plan.suggested_action,
            expected_output=expected_output,
            fulfillment_mode=fulfillment.mode,
            stop_reason="max_auto_iterations_reached",
        )

    return DelegationAssignment(
        status="running",
        next_phase=next_plan.next_phase,
        target_role=target_role,
        resolved_agent=resolved_agent,
        required_action=next_plan.suggested_action,
        expected_output=expected_output,
        fulfillment_mode=fulfillment.mode,
    )


def build_subagent_spawn_packet(
    *,
    thread: dict[str, object],
    protocol: Protocol,
    phase_cycle_state: PhaseCycleState,
    assignment: DelegationAssignment,
) -> dict[str, object] | None:
    if assignment.fulfillment_mode != "managed_agent_subagent":
        return None
    if not assignment.target_role or not assignment.subagent_profile:
        return None
    compiled_protocol = ensure_protocol_compiled(protocol)
    profile = compiled_protocol.subagent_profiles.get(assignment.subagent_profile)
    if profile is None:
        return None

    thread_id = str(thread.get("thread_id") or "")
    phase_name = phase_cycle_state.phase_name
    parent_executor = assignment.parent_executor or assignment.resolved_agent
    if not parent_executor:
        return None
    executor_id = assignment.executor_id or _dispatch_executor_id(
        thread_id=thread_id,
        phase_name=phase_name,
        cycle_index=phase_cycle_state.cycle_index,
        role=assignment.target_role,
        profile=assignment.subagent_profile,
    )
    command = _completion_command(
        thread_id=thread_id,
        agent=parent_executor,
        phase=phase_name,
        executor_type=assignment.fulfillment_mode,
        executor_id=executor_id,
        subagent_profile=assignment.subagent_profile,
        parent_executor=parent_executor,
        dispatch_id=executor_id,
    )
    instructions = _managed_subagent_instructions(
        thread_id=thread_id,
        phase_name=phase_name,
        role=assignment.target_role,
        profile_name=assignment.subagent_profile,
        profile=profile,
        parent_executor=parent_executor,
        required_action=assignment.required_action,
        expected_output=assignment.expected_output,
        completion_command=command,
    )
    codex_spawn_request: dict[str, object] = {
        "agent_type": assignment.subagent_type or "default",
        "message": instructions,
        "fork_context": False,
    }
    if profile.model:
        codex_spawn_request["model"] = profile.model
    if profile.reasoning_effort:
        codex_spawn_request["reasoning_effort"] = profile.reasoning_effort
    return {
        "packet_type": "btwin.managed_agent_subagent.dispatch",
        "version": 1,
        "dispatch": {
            "dispatch_id": executor_id,
            "thread_id": thread_id,
            "protocol": compiled_protocol.name,
            "phase": phase_name,
            "cycle_index": phase_cycle_state.cycle_index,
            "role": assignment.target_role,
            "required_action": assignment.required_action,
            "expected_output": assignment.expected_output,
            "fulfillment_mode": assignment.fulfillment_mode,
            "parent_executor": parent_executor,
            "subagent_type": assignment.subagent_type,
            "profile": assignment.subagent_profile,
            "status": "pending_result",
        },
        "executor": {
            "executor_type": assignment.fulfillment_mode,
            "executor_id": executor_id,
            "parent_executor": parent_executor,
            "suggested_contribution_agent": parent_executor,
        },
        "profile": _profile_payload(assignment.subagent_profile, profile),
        "codex_adapter": {
            "spawn_mechanism": "managed_parent_spawn_agent_tool",
            "agents_toml_schema_status": "unverified",
            "tool_policy_enforcement": "not_claimed",
            "supported_spawn_fields": [
                "agent_type",
                "message",
                "items",
                "model",
                "reasoning_effort",
                "fork_context",
            ],
        },
        "codex_spawn_request": codex_spawn_request,
        "completion_contract": {
            "result_shape": "btwin_protocol_contribution",
            "command": command,
            "blocked_reporting": (
                "Return a blocked summary to the foreground operator if the "
                "contribution cannot be recorded."
            ),
        },
        "instructions": instructions,
    }


def build_delegate_role_bindings(
    thread: dict[str, object],
    phase,
) -> dict[str, str]:
    participants = thread.get("phase_participants", [])
    if not isinstance(participants, list):
        participants = []
    if not phase.procedure:
        return {}

    bindings: dict[str, str] = {}
    for step, participant in zip(phase.procedure, participants):
        if isinstance(step.role, str) and step.role and isinstance(participant, str) and participant:
            bindings[step.role] = participant
    return bindings


def role_fulfillment_participant_violation(
    thread: dict[str, object],
    phase,
    protocol: Protocol,
) -> dict[str, object] | None:
    """Return a user-facing violation when explicit fulfillment targets an absent participant."""
    participants = _thread_participant_names(thread)
    if not phase.procedure:
        return None

    for step in phase.procedure:
        role = step.role if isinstance(step.role, str) else None
        if not role:
            continue
        fulfillment = protocol.role_fulfillment.get(role)
        if fulfillment is None:
            continue

        participant_kind = "parent" if fulfillment.parent else "agent"
        participant = fulfillment.parent or fulfillment.agent
        if not participant or participant in participants:
            continue

        phase_name = phase.name if isinstance(getattr(phase, "name", None), str) else None
        message = (
            f"role_fulfillment '{role}' resolves to {participant_kind} '{participant}', "
            f"but '{participant}' is not a participant in this thread."
        )
        return {
            "error": "role_fulfillment_participant_missing",
            "message": message,
            "role": role,
            "phase": phase_name,
            "participant_kind": participant_kind,
            "participant": participant,
            "fulfillment_mode": fulfillment.mode,
            "participants": participants,
            "hint": f"Add --participant {participant} or update role_fulfillment for role '{role}'.",
        }

    return None


def _role_fulfillment(protocol: Protocol, role: str) -> RoleFulfillment:
    return protocol.role_fulfillment.get(role) or RoleFulfillment()


def _dispatch_executor_id(
    *,
    thread_id: str,
    phase_name: str,
    cycle_index: int,
    role: str,
    profile: str,
) -> str:
    return ":".join([thread_id, phase_name, str(cycle_index), role, profile])


def _completion_command(
    *,
    thread_id: str,
    agent: str,
    phase: str,
    executor_type: str | None = None,
    executor_id: str | None = None,
    subagent_profile: str | None = None,
    parent_executor: str | None = None,
    dispatch_id: str | None = None,
) -> str:
    parts = [
        "btwin",
        "contribution",
        "submit",
        "--thread",
        shlex.quote(thread_id),
        "--agent",
        shlex.quote(agent),
        "--phase",
        shlex.quote(phase),
        "--tldr",
        shlex.quote("<summary>"),
    ]
    for option, value in (
        ("--executor-type", executor_type),
        ("--executor-id", executor_id),
        ("--subagent-profile", subagent_profile),
        ("--parent-executor", parent_executor),
        ("--dispatch-id", dispatch_id),
    ):
        if value:
            parts.extend([option, shlex.quote(value)])
    return " ".join(parts)


def _profile_payload(name: str, profile: SubagentProfile) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": name,
        "description": profile.description,
        "persona": profile.persona,
        "tools": {
            "policy_level": "declared",
            "allow": list(profile.tools.allow),
            "deny": list(profile.tools.deny),
        },
        "context": {"include": list(profile.context.include)},
    }
    if profile.model:
        payload["model"] = profile.model
    if profile.reasoning_effort:
        payload["reasoning_effort"] = profile.reasoning_effort
    return payload


def _managed_subagent_instructions(
    *,
    thread_id: str,
    phase_name: str,
    role: str,
    profile_name: str,
    profile: SubagentProfile,
    parent_executor: str,
    required_action: str | None,
    expected_output: str | None,
    completion_command: str,
) -> str:
    parts = [
        "You are a B-TWIN managed Codex sub-agent.",
        f"Parent executor: {parent_executor}",
        f"Thread: {thread_id}",
        f"Phase: {phase_name}",
        f"Role: {role}",
        f"Profile: {profile_name}",
    ]
    if profile.persona:
        parts.extend(["", "Persona:", profile.persona])
    if required_action or expected_output:
        parts.extend(["", "Assignment:"])
        if required_action:
            parts.append(f"Required action: {required_action}")
        if expected_output:
            parts.append(f"Expected output: {expected_output}")
    parts.extend(
        [
            "",
            "Tool policy is declared by B-TWIN profile metadata; do not treat it as runtime enforcement unless Codex enforces it.",
            "",
            "Completion contract:",
            completion_command,
            "",
            "Do not invent a separate B-TWIN agent identity; record the result through the parent executor identity and executor metadata above.",
            "If blocked, return a concise blocked summary to the parent executor.",
        ]
    )
    return "\n".join(parts)


def default_phase_participants(
    thread: dict[str, object],
    phase,
    *,
    protocol: Protocol | None = None,
) -> list[str]:
    names = _thread_participant_names(thread)

    if phase.procedure:
        fulfillment_matched: list[str] = []
        for step in phase.procedure:
            role = step.role if isinstance(step.role, str) else None
            fulfillment = protocol.role_fulfillment.get(role) if protocol is not None and role else None
            if fulfillment is None:
                break
            candidate = fulfillment.parent or fulfillment.agent
            if not candidate:
                break
            fulfillment_matched.append(candidate)
        if len(fulfillment_matched) == len(phase.procedure):
            return fulfillment_matched

        role_matched = [
            step.role
            for step in phase.procedure
            if isinstance(step.role, str) and step.role in names
        ]
        if len(role_matched) == len(phase.procedure):
            return role_matched

    phase_participants = thread.get("phase_participants", [])
    if isinstance(phase_participants, list) and phase_participants:
        return [name for name in phase_participants if isinstance(name, str) and name][: len(phase.procedure or [])]
    if not phase.procedure:
        return names
    return names[: len(phase.procedure)]


def _thread_participant_names(thread: dict[str, object]) -> list[str]:
    participants = thread.get("participants", [])
    if not isinstance(participants, list):
        return []
    names: list[str] = []
    for participant in participants:
        if isinstance(participant, dict):
            name = participant.get("name")
            if isinstance(name, str) and name:
                names.append(name)
            continue
        if isinstance(participant, str) and participant:
            names.append(participant)
    return names


def _current_phase_name(thread: dict[str, object], phase_cycle_state: PhaseCycleState) -> str | None:
    phase_name = thread.get("current_phase")
    if isinstance(phase_name, str) and phase_name:
        return phase_name
    return phase_cycle_state.phase_name


def _expected_output(
    *,
    phase_name: str,
    next_plan: ProtocolNextPlan,
    step_action: str | None,
    fallback: str | None,
) -> str | None:
    if next_plan.suggested_action == "submit_contribution":
        label = step_action or phase_name
        return f"{label} contribution"
    return fallback or _fallback_expected_output(phase_name)


def _manual_outcome_output(phase, next_plan: ProtocolNextPlan) -> str:
    outcomes = phase.policy_outcomes or next_plan.valid_outcomes
    if outcomes:
        return f"record outcome: {', '.join(outcomes)}"
    return "record outcome"


def _fallback_expected_output(phase_name: str) -> str:
    return f"{phase_name} contribution"


def _runtime_recovery_failed(runtime_session: Mapping[str, object] | None) -> bool:
    if runtime_session is None:
        return False
    if bool(runtime_session.get("recovery_pending")):
        return False
    if bool(runtime_session.get("degraded")) or bool(runtime_session.get("recoverable")):
        return False
    status = str(runtime_session.get("status") or "").strip().lower()
    if status not in {"failed", "closed", "ended", "exited", "terminated"}:
        return False
    transport_mode = str(runtime_session.get("transport_mode") or "").strip()
    return transport_mode == "live_process_transport"


def _loop_iteration_limit_reached(*, loop_iteration: int | None, max_auto_iterations: int) -> bool:
    if loop_iteration is None:
        return False
    if max_auto_iterations <= 0:
        return False
    return loop_iteration > max_auto_iterations


def build_delegation_resume_token(state) -> str:
    if state.last_resume_token:
        return state.last_resume_token
    return ":".join(
        [
            "delegate",
            state.thread_id,
            state.current_phase or "",
            str(state.current_cycle_index),
            str(state.loop_iteration),
            state.required_action or "",
            state.last_result_message_id or "",
        ]
    )


def build_delegation_resume_packet(
    *,
    thread: dict[str, object],
    protocol: Protocol,
    state,
    valid_outcomes: list[str] | None = None,
) -> dict[str, object]:
    thread_id = str(thread.get("thread_id") or state.thread_id)
    thread_alias = thread.get("alias")
    if not isinstance(thread_alias, str) or not thread_alias.strip():
        thread_alias = thread_id

    resume = {
        "token": build_delegation_resume_token(state),
        "target_role": state.target_role,
        "resolved_agent": state.resolved_agent,
        "required_action": state.required_action,
        "expected_output": state.expected_output,
        "why_now": _why_now(state),
        "suggested_next_command": _suggested_next_command(
            thread_id=thread_id,
            status=state.status,
            required_action=state.required_action,
            valid_outcomes=valid_outcomes or [],
        ),
    }
    if valid_outcomes:
        resume["valid_outcomes"] = list(valid_outcomes)
    if state.reason_blocked:
        resume["reason_blocked"] = state.reason_blocked

    payload = {
        "status": state.status,
        "thread": {
            "id": thread_id,
            "alias": thread_alias,
            "topic": thread.get("topic"),
        },
        "protocol": {
            "name": protocol.name,
            "phase": state.current_phase,
        },
        "resume": resume,
    }
    if state.reason_blocked:
        payload["reason_blocked"] = state.reason_blocked
    return payload


def _why_now(state) -> str:
    if state.status == "waiting_for_human" and state.required_action == "record_outcome":
        return "phase requirements are met and a human outcome is required to continue"
    if state.status == "blocked":
        reason = state.reason_blocked or state.stop_reason or "unknown"
        return f"delegation is blocked: {reason}"
    if state.status == "running":
        return "delegation is currently assigned to the helper"
    if state.status == "completed":
        return "delegation finished for the current thread state"
    return "delegation is paused and awaiting the next operator action"


def _suggested_next_command(
    *,
    thread_id: str,
    status: str,
    required_action: str | None,
    valid_outcomes: list[str],
) -> str:
    if status == "waiting_for_human" and required_action == "record_outcome":
        if valid_outcomes:
            options = "|".join(valid_outcomes)
            return f"btwin delegate respond --thread {thread_id} --outcome <{options}>"
        return f"btwin delegate respond --thread {thread_id} --outcome <outcome>"
    return f"btwin delegate status --thread {thread_id}"
