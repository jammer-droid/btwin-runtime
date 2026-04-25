"""Protocol flow planning helpers for next-action decisions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from btwin_core.protocol_store import Protocol, ProtocolPhase, ensure_protocol_compiled
from btwin_core.protocol_validator import ProtocolValidator

ProtocolSuggestedAction = Literal["submit_contribution", "advance_phase", "record_outcome", "close_thread"]


class ProtocolNextPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    protocol: str
    current_phase: str | None
    passed: bool
    missing: list[dict[str, object]] = Field(default_factory=list)
    valid_outcomes: list[str] = Field(default_factory=list)
    requested_outcome: str | None = None
    next_phase: str | None = None
    suggested_action: ProtocolSuggestedAction
    error: str | None = None
    manual_outcome_required: bool = False
    guard_set: str | None = None
    declared_guards: list[str] = Field(default_factory=list)
    outcome_policy: str | None = None
    outcome_emitters: list[str] = Field(default_factory=list)
    outcome_actions: list[str] = Field(default_factory=list)
    policy_outcomes: list[str] = Field(default_factory=list)
    hint: str | None = None


def resolve_phase_runtime_metadata(
    protocol: Protocol, phase_name: str | None
) -> tuple[str | None, list[str], str | None, list[str], list[str], list[str]]:
    phase = resolve_protocol_phase(protocol, phase_name)
    if phase is None:
        return None, [], None, [], [], []
    return (
        phase.guard_set,
        list(phase.declared_guards),
        phase.outcome_policy,
        list(phase.outcome_emitters),
        list(phase.outcome_actions),
        list(phase.policy_outcomes),
    )


def resolve_protocol_phase(protocol: Protocol, phase_name: str | None) -> ProtocolPhase | None:
    if not phase_name:
        return None
    return next((item for item in protocol.phases if item.name == phase_name), None)


def _guard_note(*, guard_set: str | None, declared_guards: list[str]) -> str:
    if declared_guards:
        return "baseline runtime guard remains always-on; protocol-declared guards are additive in v1."
    if guard_set:
        return "baseline runtime guard remains always-on; this phase does not declare additional protocol guards."
    return "baseline runtime guard remains always-on; no protocol-declared guard set is referenced for this phase."


def _next_plan_hint(thread_id: str, plan: ProtocolNextPlan) -> str | None:
    note = _guard_note(guard_set=plan.guard_set, declared_guards=plan.declared_guards)

    if plan.error == "phase_not_found":
        return f"Check `btwin thread show {thread_id}` and `btwin protocol next --thread {thread_id}`."

    if not plan.passed:
        agent_name = None
        if plan.missing and isinstance(plan.missing[0], dict):
            agent = plan.missing[0].get("agent")
            if isinstance(agent, str) and agent:
                agent_name = agent
        hint = (
            f"Try `btwin contribution submit --thread {thread_id} --agent {agent_name or '<agent>'} "
            f"--phase {plan.current_phase or '<phase>'}` with the required sections."
        )
        return f"{hint} {note}"

    if plan.error == "unsupported_outcome" and plan.valid_outcomes:
        options = " | ".join(plan.valid_outcomes)
        hint = f"Re-run `btwin protocol apply-next --thread {thread_id} --outcome <{options}>` with one of the valid outcomes."
        return f"{hint} {note}"

    if plan.suggested_action == "record_outcome" and plan.valid_outcomes:
        options = " | ".join(plan.valid_outcomes)
        hint = f"Choose an outcome and re-run `btwin protocol apply-next --thread {thread_id} --outcome <{options}>`."
        return f"{hint} {note}"

    if plan.suggested_action == "advance_phase":
        suffix = f" to move into `{plan.next_phase}`" if plan.next_phase else ""
        hint = f"Try `btwin protocol apply-next --thread {thread_id}`{suffix}."
        return f"{hint} {note}" if note else hint

    if plan.suggested_action == "close_thread":
        hint = f"Try `btwin thread close --thread {thread_id} --summary \"...\"`."
        return f"{hint} {note}"

    return note


def describe_next(
    thread: dict,
    protocol: Protocol,
    contributions: list[dict],
    *,
    outcome: str | None = None,
) -> ProtocolNextPlan:
    """Describe the next valid protocol action for a thread."""
    protocol = ensure_protocol_compiled(protocol)
    thread_id = str(thread.get("thread_id") or "")
    current_phase = thread.get("current_phase")

    if not thread_id:
        raise ValueError("thread must include a thread_id")

    if not isinstance(current_phase, str) or not current_phase:
        return ProtocolNextPlan(
            thread_id=thread_id,
            protocol=protocol.name,
            current_phase=current_phase if isinstance(current_phase, str) else None,
            passed=False,
            suggested_action="record_outcome",
            error="phase_not_found",
            requested_outcome=outcome,
        )

    phase = resolve_protocol_phase(protocol, current_phase)
    if phase is None:
        return ProtocolNextPlan(
            thread_id=thread_id,
            protocol=protocol.name,
            current_phase=current_phase,
            passed=False,
            suggested_action="record_outcome",
            error="phase_not_found",
            requested_outcome=outcome,
            hint=_next_plan_hint(thread_id, ProtocolNextPlan(
                thread_id=thread_id,
                protocol=protocol.name,
                current_phase=current_phase,
                passed=False,
                suggested_action="record_outcome",
                error="phase_not_found",
                requested_outcome=outcome,
            )),
        )

    (
        guard_set,
        declared_guards,
        outcome_policy,
        outcome_emitters,
        outcome_actions,
        policy_outcomes,
    ) = resolve_phase_runtime_metadata(protocol, current_phase)

    phase_participants = thread.get("phase_participants", [])
    if not isinstance(phase_participants, list):
        phase_participants = []

    validation = ProtocolValidator.validate_phase(
        phase_participants=[str(name) for name in phase_participants if isinstance(name, str)],
        template_sections=phase.template or [],
        contributions=contributions,
    )

    phase_index = next((idx for idx, item in enumerate(protocol.phases) if item.name == current_phase), -1)
    sequential_next = protocol.phases[phase_index + 1].name if 0 <= phase_index < len(protocol.phases) - 1 else None
    branch_transitions = [t for t in protocol.transitions if t.from_phase == current_phase and t.on]
    default_transition = next((t for t in protocol.transitions if t.from_phase == current_phase and t.on is None), None)
    if policy_outcomes:
        valid_outcomes = list(policy_outcomes)
    elif branch_transitions and not protocol.outcome_policies:
        valid_outcomes = list(protocol.outcomes) or [
            transition.on for transition in branch_transitions if transition.on
        ]
    elif branch_transitions:
        valid_outcomes = [transition.on for transition in branch_transitions if transition.on]
    elif protocol.outcome_policies:
        valid_outcomes = []
    else:
        valid_outcomes = list(protocol.outcomes)

    next_phase = None
    suggested_action: ProtocolSuggestedAction = "close_thread"
    manual_outcome_required = False
    if not validation.passed:
        suggested_action = "submit_contribution"
    elif outcome:
        if not valid_outcomes or outcome not in valid_outcomes:
            return ProtocolNextPlan(
                thread_id=thread_id,
                protocol=protocol.name,
                current_phase=current_phase,
                passed=validation.passed,
                missing=validation.missing,
                valid_outcomes=[str(outcome_value) for outcome_value in valid_outcomes if outcome_value],
                requested_outcome=outcome,
                suggested_action="record_outcome",
                error="unsupported_outcome",
                guard_set=guard_set,
                declared_guards=declared_guards,
                outcome_policy=outcome_policy,
                outcome_emitters=outcome_emitters,
                outcome_actions=outcome_actions,
                policy_outcomes=policy_outcomes,
                hint=_next_plan_hint(
                    thread_id,
                    ProtocolNextPlan(
                        thread_id=thread_id,
                        protocol=protocol.name,
                        current_phase=current_phase,
                        passed=validation.passed,
                        missing=validation.missing,
                        valid_outcomes=[str(outcome_value) for outcome_value in valid_outcomes if outcome_value],
                        requested_outcome=outcome,
                        suggested_action="record_outcome",
                        error="unsupported_outcome",
                        guard_set=guard_set,
                        declared_guards=declared_guards,
                        outcome_policy=outcome_policy,
                        outcome_emitters=outcome_emitters,
                        outcome_actions=outcome_actions,
                        policy_outcomes=policy_outcomes,
                    ),
                ),
            )
        matched = next((t for t in branch_transitions if t.on == outcome), None)
        next_phase = matched.to if matched else None
        if next_phase:
            suggested_action = "advance_phase"
        else:
            suggested_action = "record_outcome"
            manual_outcome_required = True
    elif valid_outcomes:
        suggested_action = "record_outcome"
        manual_outcome_required = True
    else:
        next_phase = default_transition.to if default_transition else sequential_next
        if next_phase:
            suggested_action = "advance_phase"

    plan = ProtocolNextPlan(
        thread_id=thread_id,
        protocol=protocol.name,
        current_phase=current_phase,
        passed=validation.passed,
        missing=validation.missing,
        valid_outcomes=[str(outcome_value) for outcome_value in valid_outcomes if outcome_value],
        requested_outcome=outcome,
        next_phase=next_phase,
        suggested_action=suggested_action,
        manual_outcome_required=manual_outcome_required,
        guard_set=guard_set,
        declared_guards=declared_guards,
        outcome_policy=outcome_policy,
        outcome_emitters=outcome_emitters,
        outcome_actions=outcome_actions,
        policy_outcomes=policy_outcomes,
    )
    plan.hint = _next_plan_hint(thread_id, plan)
    return plan
