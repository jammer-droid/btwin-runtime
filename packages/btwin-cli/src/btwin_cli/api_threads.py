"""HTTP API for thread collaboration — REST + EventBus integration."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from btwin_core.context_core import ContextCore
from btwin_core.delegation_engine import (
    DelegationAssignment,
    build_delegation_assignment,
    build_subagent_spawn_packet,
    build_delegation_resume_packet,
    build_delegation_resume_token,
    default_phase_participants,
)
from btwin_core.delegation_state import DelegationState, delegation_status_payload
from btwin_core.delegation_store import DelegationStore
from btwin_core.event_bus import EventBus, SSEEvent
from btwin_core.locale_settings import LocaleSettingsStore
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_engine import (
    advance_phase_cycle,
    build_phase_cycle_context_core,
)
from btwin_core.phase_cycle_store import PhaseCycleStore
from btwin_core.phase_context import PhaseContextBuilder
from btwin_core.protocol_flow import describe_next
from btwin_core.protocol_store import (
    Protocol,
    ProtocolPhase,
    ProtocolStore,
    build_protocol_preview,
    compile_protocol_definition,
)
from btwin_core.protocol_validator import ProtocolValidator
from btwin_core.system_mailbox_store import SystemMailboxStore
from btwin_core.thread_store import ThreadStore
from btwin_core.thread_summarizer import ThreadSummarizer
from btwin_core.workflow_event_log import WorkflowEventLog
from btwin_core.workflow_constraints import (
    validate_contribution_submission,
    validate_direct_message_targets,
    validate_thread_close,
)
from btwin_cli.phase_cycle_visual import build_phase_cycle_visual_payload

logger = logging.getLogger(__name__)

_RUNTIME_SESSION_FIELDS = (
    "thread_id",
    "provider",
    "primary_transport_mode",
    "transport_mode",
    "fallback_mode",
    "status",
    "provider_session_id",
    "last_activity_at",
    "auth_mode",
    "gateway_mode",
    "gateway_route",
    "transport_capability",
    "continuity_mode",
    "launch_strategy",
    "last_transport_error",
    "degraded",
    "recoverable",
    "recovery_attempts",
    "recovery_pending",
    "recovery_target_transport_mode",
    "workspace_root",
    "helper_launch_cwd",
)


def _runtime_session_fields(session: Any) -> dict[str, object]:
    if isinstance(session, dict):
        getter = session.get
    else:
        getter = lambda key: getattr(session, key, None)
    return {
        key: value
        for key in _RUNTIME_SESSION_FIELDS
        if (value := getter(key)) is not None
    }


def _normalize_runtime_session_record(session: Any) -> dict[str, object]:
    record = _runtime_session_fields(session)
    if "primary_transport_mode" not in record and "transport_mode" in record:
        record["primary_transport_mode"] = record["transport_mode"]

    for key in ("auth_mode", "gateway_mode", "gateway_route"):
        if key in record and record[key] is None:
            record.pop(key)
    fallback_mode = record.get("fallback_mode")
    transport_mode = record.get("transport_mode")
    record["fallback_transport_involved"] = (
        isinstance(fallback_mode, str)
        and bool(fallback_mode)
        and transport_mode == fallback_mode
    )
    record.setdefault("degraded", bool(record["fallback_transport_involved"]))
    record.setdefault("recoverable", False)
    record.setdefault("recovery_attempts", 0)
    record.setdefault("recovery_pending", False)
    record.setdefault("recovery_target_transport_mode", None)
    return record


def _normalize_runtime_sessions_by_agent(
    sessions_by_agent: dict[str, list[dict[str, object] | object]],
) -> dict[str, list[dict[str, object]]]:
    return {
        agent_name: [_normalize_runtime_session_record(session) for session in sessions]
        for agent_name, sessions in sessions_by_agent.items()
    }


def _find_runtime_session_record(
    agent_runner: Any,
    *,
    thread_id: str,
    agent_name: str,
) -> dict[str, object] | None:
    if not hasattr(agent_runner, "list_runtime_sessions_by_agent"):
        return None

    sessions_by_agent = _normalize_runtime_sessions_by_agent(
        agent_runner.list_runtime_sessions_by_agent()
    )
    for session in sessions_by_agent.get(agent_name, []):
        if session.get("thread_id") == thread_id:
            return session
    return None


def _enrich_runtime_event(event: SSEEvent, agent_runner: Any | None) -> SSEEvent:
    if (
        agent_runner is None
        or event.type != "agent_session_state"
        or not isinstance(event.metadata, dict)
    ):
        return event

    agent_name = event.metadata.get("agent_name")
    if not isinstance(agent_name, str) or not agent_name:
        return event

    session = _find_runtime_session_record(
        agent_runner,
        thread_id=event.resource_id,
        agent_name=agent_name,
    )
    if session is None:
        return event

    metadata = dict(event.metadata)
    for key in (
        "provider",
        "primary_transport_mode",
        "transport_mode",
        "fallback_mode",
        "fallback_transport_involved",
        "auth_mode",
        "gateway_mode",
        "gateway_route",
        "transport_capability",
        "continuity_mode",
        "launch_strategy",
        "last_transport_error",
        "degraded",
        "recoverable",
        "recovery_attempts",
        "workspace_root",
        "helper_launch_cwd",
    ):
        value = session.get(key)
        if value is not None:
            metadata[key] = value

    return SSEEvent(
        type=event.type,
        resource_id=event.resource_id,
        timestamp=event.timestamp,
        metadata=metadata,
    )


def _install_runtime_event_enricher(event_bus: EventBus, agent_runner: Any | None) -> None:
    if agent_runner is None or getattr(event_bus, "_btwin_runtime_event_enricher_installed", False):
        return

    original_publish = event_bus.publish

    def publish(event: SSEEvent) -> None:
        original_publish(_enrich_runtime_event(event, agent_runner))

    event_bus.publish = publish  # type: ignore[method-assign]
    setattr(event_bus, "_btwin_runtime_event_enricher_installed", True)


def _build_phase_cycle_context_core(
    *,
    thread: dict[str, object],
    protocol: Protocol | None,
    phase: ProtocolPhase,
    state: PhaseCycleState,
) -> ContextCore:
    return build_phase_cycle_context_core(
        thread=thread,
        protocol=protocol,
        phase=phase,
        state=state,
    )


def _build_phase_cycle_visual(
    *,
    protocol: Protocol | None,
    phase: ProtocolPhase | None,
    state: PhaseCycleState,
) -> dict[str, object]:
    return build_phase_cycle_visual_payload(protocol=protocol, phase=phase, state=state)


def _build_delegate_role_bindings(thread: dict[str, object], phase: ProtocolPhase) -> dict[str, str]:
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


def _validate_delegate_direct_message(
    *,
    thread: dict[str, object],
    protocol: Protocol,
    from_agent: str,
    target_agents: list[str],
    phase_name: str | None = None,
) -> Any:
    validation_thread = dict(thread)
    if phase_name is not None:
        validation_thread["current_phase"] = phase_name
    return validate_direct_message_targets(
        thread=validation_thread,
        protocol=protocol,
        from_agent=from_agent,
        target_agents=target_agents,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _delegate_dispatch_client_message_id(
    *,
    thread_id: str,
    phase_cycle_state: PhaseCycleState,
    assignment: DelegationAssignment,
) -> str:
    return ":".join(
        [
            "delegate",
            "start",
            thread_id,
            str(phase_cycle_state.cycle_index),
            phase_cycle_state.phase_name or "",
            assignment.target_role or "",
            assignment.resolved_agent or "",
            assignment.required_action or "",
        ]
    )


def _delegate_dispatch_content(
    *,
    assignment: DelegationAssignment,
    phase_cycle_state: PhaseCycleState,
    heading: str = "Delegation Start",
    human_summary: str | None = None,
    spawn_packet: dict[str, object] | None = None,
) -> tuple[str, str]:
    target_role = assignment.target_role or "unassigned"
    resolved_agent = assignment.resolved_agent or "unassigned"
    required_action = assignment.required_action or "continue"
    expected_output = assignment.expected_output or "n/a"
    content = (
        f"## {heading}\n\n"
        f"Role: {target_role}\n\n"
        f"Agent: @{resolved_agent}\n\n"
        f"Phase: {phase_cycle_state.phase_name}\n\n"
        f"Action: {required_action}\n\n"
        f"Expected output: {expected_output}\n"
    )
    if human_summary:
        content += f"\nHuman input: {human_summary}\n"
    if assignment.fulfillment_mode == "managed_agent_subagent":
        content += "\nSub-agent dispatch: spawn the requested B-TWIN managed Codex sub-agent and pass through this packet.\n"
    if spawn_packet is not None:
        content += "\n```json\n"
        content += json.dumps(spawn_packet, ensure_ascii=False, indent=2)
        content += "\n```\n"
    tldr = f"delegate {phase_cycle_state.phase_name} -> {resolved_agent}"
    return content, tldr


def _delegate_dispatch_exists(
    thread_store: ThreadStore,
    *,
    thread_id: str,
    client_message_id: str,
) -> bool:
    return any(
        message.get("client_message_id") == client_message_id
        for message in thread_store.list_messages(thread_id)
    )


def _dispatch_delegate_assignment(
    thread_store: ThreadStore,
    *,
    thread: dict[str, object],
    protocol: Protocol,
    thread_id: str,
    assignment: DelegationAssignment,
    phase_cycle_state: PhaseCycleState,
    routing_source: str = "btwin.delegate.start",
    human_summary: str | None = None,
) -> tuple[bool, Any | None]:
    if assignment.status != "running" or not assignment.resolved_agent:
        return False, None

    client_message_id = _delegate_dispatch_client_message_id(
        thread_id=thread_id,
        phase_cycle_state=phase_cycle_state,
        assignment=assignment,
    )
    if _delegate_dispatch_exists(thread_store, thread_id=thread_id, client_message_id=client_message_id):
        return False, None

    content, tldr = _delegate_dispatch_content(
        assignment=assignment,
        phase_cycle_state=phase_cycle_state,
        heading="Delegation Resume" if human_summary else "Delegation Start",
        human_summary=human_summary,
        spawn_packet=build_subagent_spawn_packet(
            thread=thread,
            protocol=protocol,
            phase_cycle_state=phase_cycle_state,
            assignment=assignment,
        ),
    )
    try:
        msg = thread_store.send_message(
            thread_id=thread_id,
            from_agent="btwin",
            content=content,
            tldr=tldr,
            client_message_id=client_message_id,
            msg_type="delegation",
            delivery_mode="direct",
            target_agents=[assignment.resolved_agent],
            routing_source=routing_source,
            routing_reason="delegate_assignment",
            message_phase=phase_cycle_state.phase_name,
            state_affecting=False,
        )
    except Exception:
        logger.warning("Delegation dispatch failed for thread %s", thread_id, exc_info=True)
        return False, {"error": "dispatch_failed"}
    if msg is None:
        return False, {"error": "dispatch_failed"}
    return True, None


def _resolve_delegate_phase(
    *,
    thread: dict[str, object],
    protocol: Protocol,
    phase_cycle_state: PhaseCycleState | None,
) -> ProtocolPhase | None:
    if phase_cycle_state is not None:
        phase_name = phase_cycle_state.phase_name
        if isinstance(phase_name, str) and phase_name:
            phase = next((item for item in protocol.phases if item.name == phase_name), None)
            if phase is not None:
                return phase

    thread_phase_name = thread.get("current_phase")
    if isinstance(thread_phase_name, str) and thread_phase_name:
        phase = next((item for item in protocol.phases if item.name == thread_phase_name), None)
        if phase is not None:
            return phase

    return None


def _delegation_state_from_assignment(
    *,
    thread: dict[str, object],
    protocol: Protocol,
    thread_id: str,
    phase_cycle_state: PhaseCycleState,
    assignment: DelegationAssignment,
) -> DelegationState:
    spawn_packet = build_subagent_spawn_packet(
        thread=thread,
        protocol=protocol,
        phase_cycle_state=phase_cycle_state,
        assignment=assignment,
    )
    return DelegationState(
        thread_id=thread_id,
        status=assignment.status,
        updated_at=_iso_now(),
        loop_iteration=phase_cycle_state.cycle_index,
        current_phase=phase_cycle_state.phase_name,
        current_cycle_index=phase_cycle_state.cycle_index,
        target_role=assignment.target_role,
        resolved_agent=assignment.resolved_agent,
        required_action=assignment.required_action,
        expected_output=assignment.expected_output,
        fulfillment_mode=assignment.fulfillment_mode,
        parent_executor=assignment.parent_executor,
        subagent_profile=assignment.subagent_profile,
        subagent_type=assignment.subagent_type,
        executor_id=assignment.executor_id,
        spawn_packet=spawn_packet,
        reason_blocked=assignment.reason_blocked,
    )


class ThreadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    topic: str
    protocol: str
    participants: list[str] | None = None


class ThreadJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    agent_name: str = Field(alias="agentName")


class ThreadCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    summary: str
    decision: str | None = None
    force: bool = False
    source: str | None = None


class MessageSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_agent: str = Field(alias="fromAgent")
    content: str
    tldr: str
    client_message_id: str | None = Field(default=None, alias="clientMessageId")
    msg_type: str = Field(default="message", alias="msgType")
    reply_to: str | None = Field(default=None, alias="replyTo")
    delivery_mode: str = Field(default="auto", alias="deliveryMode")
    target_agents: list[str] = Field(default_factory=list, alias="targetAgents")


class ContributionSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    agent_name: str = Field(alias="agentName")
    phase: str
    content: str
    tldr: str
    executor_type: str | None = Field(default=None, alias="executorType")
    executor_id: str | None = Field(default=None, alias="executorId")
    subagent_profile: str | None = Field(default=None, alias="subagentProfile")
    parent_executor: str | None = Field(default=None, alias="parentExecutor")
    dispatch_id: str | None = Field(default=None, alias="dispatchId")


class AdvancePhaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    next_phase: str = Field(alias="nextPhase")


class DelegateRespondRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    outcome: str
    summary: str | None = None
    resume_token: str | None = Field(default=None, alias="resumeToken")


class DelegateResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    bypass_permissions: bool | None = Field(default=None, alias="bypassPermissions")
    project_root: str | None = Field(default=None, alias="projectRoot")


class SpawnAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    agent_name: str = Field(alias="agentName")
    bypass_permissions: bool | None = Field(default=None, alias="bypassPermissions")
    project_root: str | None = Field(default=None, alias="projectRoot")


class RecoverAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    agent_name: str = Field(alias="agentName")
    bypass_permissions: bool | None = Field(default=None, alias="bypassPermissions")
    project_root: str | None = Field(default=None, alias="projectRoot")


class InteractionModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    interaction_mode: str = Field(alias="interactionMode")


def create_threads_router(
    thread_store: ThreadStore,
    protocol_store: ProtocolStore,
    event_bus: EventBus,
    btwin_factory: Callable[[], Any] | None = None,
    agent_store: Any | None = None,
    agent_runner: Any | None = None,
) -> APIRouter:
    router = APIRouter()
    _install_runtime_event_enricher(event_bus, agent_runner)
    locale_settings_store = LocaleSettingsStore(thread_store.data_dir)
    system_mailbox_store = SystemMailboxStore(thread_store.data_dir)
    phase_cycle_store = PhaseCycleStore(thread_store.data_dir)
    delegation_store = DelegationStore(thread_store.data_dir)

    @router.get("/api/protocols")
    def list_protocols():
        return protocol_store.list_protocols()

    @router.get("/api/protocols/{name}")
    def get_protocol(name: str):
        proto = protocol_store.get_protocol(name)
        if proto is None:
            raise HTTPException(status_code=404, detail=f"Protocol '{name}' not found")
        return proto.model_dump(by_alias=True)

    @router.post("/api/threads")
    async def create_thread(req: ThreadCreateRequest):
        proto = protocol_store.get_protocol(req.protocol)
        if proto is None:
            raise HTTPException(status_code=404, detail=f"Protocol '{req.protocol}' not found")
        initial_phase = proto.phases[0].name if proto.phases else None
        initial_phase_def = proto.phases[0] if proto.phases else None
        phase_participants = (
            default_phase_participants({"participants": req.participants or []}, initial_phase_def)
            if initial_phase_def is not None
            else None
        )

        thread = thread_store.create_thread(
            topic=req.topic,
            protocol=req.protocol,
            participants=req.participants,
            initial_phase=initial_phase,
            phase_participants=phase_participants,
            locale=locale_settings_store.read().model_dump(),
        )
        event_bus.publish(SSEEvent(type="thread_created", resource_id=thread["thread_id"]))
        return thread

    @router.get("/api/threads")
    def list_threads(status: str | None = None):
        return thread_store.list_threads(status=status)

    @router.get("/api/threads/{thread_id}")
    def get_thread(thread_id: str):
        thread = thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        return thread

    @router.post("/api/threads/{thread_id}/interaction-mode")
    async def set_interaction_mode(thread_id: str, req: InteractionModeRequest):
        if req.interaction_mode not in {"discuss", "contribute"}:
            raise HTTPException(status_code=422, detail="interactionMode must be 'discuss' or 'contribute'")

        updated = thread_store.set_interaction_mode(thread_id, req.interaction_mode)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        event_bus.publish(
            SSEEvent(
                type="thread_mode_changed",
                resource_id=thread_id,
                metadata={"interaction_mode": req.interaction_mode},
            )
        )
        return updated

    @router.post("/api/threads/{thread_id}/join")
    async def join_thread(thread_id: str, req: ThreadJoinRequest):
        updated = thread_store.join_thread(thread_id, agent_name=req.agent_name)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        event_bus.publish(SSEEvent(type="thread_updated", resource_id=thread_id))
        return updated

    @router.post("/api/threads/{thread_id}/close")
    async def close_thread(thread_id: str, req: ThreadCloseRequest):
        thread = thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        proto = protocol_store.get_protocol(thread.get("protocol", ""))
        if proto is not None and not req.force:
            current_phase = thread.get("current_phase")
            contributions = (
                thread_store.list_contributions(thread_id, phase=current_phase)
                if isinstance(current_phase, str) and current_phase
                else []
            )
            violation = validate_thread_close(thread=thread, protocol=proto, contributions=contributions)
            if violation is not None:
                raise HTTPException(status_code=409, detail=violation.model_dump())
        closed = thread_store.close_thread(thread_id, summary=req.summary, decision=req.decision)
        if closed is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        if req.force:
            WorkflowEventLog(thread_store.workflow_event_log_path(thread_id)).append(
                {
                    "timestamp": closed.get("closed_at"),
                    "thread_id": thread_id,
                    "event_type": "thread_force_closed",
                    "source": f"btwin.{req.source}" if req.source else "btwin",
                    "summary": req.summary,
                    "decision": req.decision,
                }
            )

        result_record_id = None
        if btwin_factory is not None:
            try:
                twin = btwin_factory()
                protocol_name = closed.get("protocol", "unknown")
                participants = [participant["name"] for participant in closed.get("participants", [])]

                content = f"## Summary\n\n{req.summary}"
                if req.decision:
                    content += f"\n\n## Decision\n\n{req.decision}"
                content += f"\n\n## Participants\n\n{', '.join(participants)}"
                content += f"\n\n## Thread\n\n{thread_id} (protocol: {protocol_name})"

                tldr = req.summary[:200]
                result = twin.record(
                    content,
                    topic="thread-result",
                    tags=["thread-result", f"protocol:{protocol_name}"],
                    tldr=tldr,
                )
                saved_path = result.get("path")
                if saved_path:
                    saved_text = Path(saved_path).read_text(encoding="utf-8")
                    if saved_text.startswith("---\n"):
                        import yaml

                        parts = saved_text.split("---\n", 2)
                        if len(parts) >= 3:
                            frontmatter = yaml.safe_load(parts[1])
                            if frontmatter:
                                candidate_record_id = frontmatter.get("record_id")
                                if candidate_record_id:
                                    update_result = twin.update_entry(
                                        record_id=candidate_record_id,
                                        related_records=[f"thread:{thread_id}"],
                                    )
                                    if isinstance(update_result, dict) and update_result.get("ok"):
                                        result_record_id = candidate_record_id
            except Exception:
                logger.warning("Failed to create thread result entry for %s", thread_id, exc_info=True)

        event_bus.publish(
            SSEEvent(
                type="thread_closed",
                resource_id=thread_id,
                metadata={"summary": req.summary[:100], "force": req.force, "source": req.source},
            )
        )
        response = dict(closed)
        if result_record_id:
            response["result_record_id"] = result_record_id
        return response

    @router.post("/api/threads/{thread_id}/messages")
    async def send_message(thread_id: str, req: MessageSendRequest):
        thread = thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")

        if req.delivery_mode not in {"auto", "broadcast", "direct"}:
            raise HTTPException(status_code=422, detail="deliveryMode must be 'auto', 'broadcast', or 'direct'")
        if req.delivery_mode == "direct" and not req.target_agents:
            raise HTTPException(status_code=422, detail="direct delivery requires at least one target agent")
        if req.delivery_mode == "direct":
            participant_names = {participant["name"] for participant in thread.get("participants", [])}
            unknown_targets = sorted(target for target in req.target_agents if target not in participant_names)
            if unknown_targets:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown target agents: {', '.join(unknown_targets)}",
                )
            proto = protocol_store.get_protocol(thread.get("protocol", ""))
            if proto is not None:
                violation = _validate_delegate_direct_message(
                    thread=thread,
                    protocol=proto,
                    from_agent=req.from_agent,
                    target_agents=req.target_agents,
                )
                if violation is not None:
                    raise HTTPException(status_code=409, detail=violation.model_dump())

        msg = thread_store.send_message(
            thread_id=thread_id,
            from_agent=req.from_agent,
            content=req.content,
            tldr=req.tldr,
            client_message_id=req.client_message_id,
            msg_type=req.msg_type,
            reply_to=req.reply_to,
            delivery_mode=req.delivery_mode,
            target_agents=req.target_agents,
        )
        if msg is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        event_bus.publish(
            SSEEvent(
                type="message_sent",
                resource_id=thread_id,
                metadata={
                    "message_id": msg["message_id"],
                    "from_agent": req.from_agent,
                    "client_message_id": req.client_message_id,
                    "tldr": req.tldr,
                    "msg_type": req.msg_type,
                    "content": req.content,
                    "delivery_mode": req.delivery_mode,
                    "target_agents": req.target_agents,
                    "chain_depth": 0,
                },
            )
        )
        return msg

    @router.get("/api/threads/{thread_id}/messages")
    def list_messages(thread_id: str, since: str | None = None):
        return thread_store.list_messages(thread_id, since=since)

    @router.get("/api/threads/{thread_id}/inbox")
    def thread_inbox(thread_id: str, agent: str):
        messages = thread_store.list_inbox(thread_id, agent)
        if messages is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' or participant '{agent}' not found")
        return {
            "thread_id": thread_id,
            "agent": agent,
            "pending_count": len(messages),
            "messages": messages,
        }

    @router.get("/api/system-mailbox")
    def list_system_mailbox(threadId: str | None = None, limit: int = 20):
        reports = system_mailbox_store.list_reports(thread_id=threadId, limit=limit)
        return {"count": len(reports), "reports": reports}

    @router.get("/api/threads/{thread_id}/phase-cycle")
    def get_phase_cycle(thread_id: str):
        thread = thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        state = phase_cycle_store.read(thread_id)
        if state is None:
            return {"state": None}
        protocol_name = thread.get("protocol")
        protocol = protocol_store.get_protocol(protocol_name) if isinstance(protocol_name, str) else None
        if protocol is None:
            return {"state": state.model_dump(), "visual": _build_phase_cycle_visual(protocol=None, phase=None, state=state)}
        current_phase = thread.get("current_phase")
        phase = next((item for item in protocol.phases if item.name == current_phase), None)
        if phase is None:
            return {"state": state.model_dump(), "visual": _build_phase_cycle_visual(protocol=protocol, phase=None, state=state)}
        context_core = _build_phase_cycle_context_core(thread=thread, protocol=protocol, phase=phase, state=state)
        return {
            "state": state.model_dump(),
            "context_core": context_core.model_dump(),
            "visual": _build_phase_cycle_visual(protocol=protocol, phase=phase, state=state),
        }

    @router.post("/api/threads/{thread_id}/delegate/start")
    def start_delegate(thread_id: str):
        thread = thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        if thread.get("status") != "active":
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found or closed")

        protocol_name = thread.get("protocol")
        protocol = protocol_store.get_protocol(protocol_name) if isinstance(protocol_name, str) else None
        if protocol is None:
            raise HTTPException(status_code=404, detail=f"Protocol '{protocol_name}' not found")

        phase_cycle_state = phase_cycle_store.read(thread_id)
        phase = _resolve_delegate_phase(thread=thread, protocol=protocol, phase_cycle_state=phase_cycle_state)
        if phase is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "phase_not_found",
                    "current_phase": thread.get("current_phase"),
                    "phase_cycle_state": phase_cycle_state.phase_name if phase_cycle_state is not None else None,
                },
            )

        if phase_cycle_state is None:
            phase_cycle_state = phase_cycle_store.start_cycle(
                thread_id=thread_id,
                phase_name=phase.name,
                procedure_steps=[step.action for step in phase.procedure or []],
            )

        assignment_thread = dict(thread)
        if phase_cycle_state is not None and isinstance(phase_cycle_state.phase_name, str) and phase_cycle_state.phase_name:
            assignment_thread["current_phase"] = phase_cycle_state.phase_name

        contributions = thread_store.list_contributions(thread_id, phase=phase.name)
        assignment = build_delegation_assignment(
            thread=assignment_thread,
            protocol=protocol,
            phase_cycle_state=phase_cycle_state,
            role_bindings=_build_delegate_role_bindings(thread, phase),
            contributions=contributions,
        )
        state = _delegation_state_from_assignment(
            thread=thread,
            protocol=protocol,
            thread_id=thread_id,
            phase_cycle_state=phase_cycle_state,
            assignment=assignment,
        )
        dispatched = False
        if assignment.status == "running" and assignment.fulfillment_mode in {"registered_agent", "managed_agent_subagent"}:
            dispatched, dispatch_violation = _dispatch_delegate_assignment(
                thread_store,
                thread=thread,
                protocol=protocol,
                thread_id=thread_id,
                assignment=assignment,
                phase_cycle_state=phase_cycle_state,
            )
            if dispatch_violation is not None:
                reason = None
                if isinstance(dispatch_violation, dict):
                    reason = dispatch_violation.get("error")
                else:
                    reason = getattr(dispatch_violation, "error", None)
                blocked_state = state.model_copy(
                    update={"status": "blocked", "reason_blocked": reason or "dispatch_failed"}
                )
                delegation_store.write(blocked_state)
                raise HTTPException(status_code=409, detail=blocked_state.model_dump(exclude_none=True))
        delegation_store.write(state)
        if assignment.status == "running" and dispatched:
            event_bus.publish(
                SSEEvent(
                    type="thread_updated",
                    resource_id=thread_id,
                    metadata={
                        "agent_dispatched": assignment.resolved_agent,
                        "delegation_status": state.status,
                    },
                )
            )
        return state.model_dump(exclude_none=True)

    @router.get("/api/threads/{thread_id}/delegate/status")
    def get_delegate_status(thread_id: str):
        state = delegation_store.read(thread_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Delegation state for thread '{thread_id}' not found")
        return delegation_status_payload(state)

    @router.post("/api/threads/{thread_id}/delegate/resume")
    async def resume_delegate(thread_id: str, req: DelegateResumeRequest):
        if thread_store.get_thread(thread_id) is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        if agent_runner is None or not hasattr(agent_runner, "resume_running_delegation"):
            raise HTTPException(status_code=503, detail="Agent runner delegation resume not configured")
        payload = await agent_runner.resume_running_delegation(
            thread_id,
            bypass_permissions=req.bypass_permissions,
            workspace_root=Path(req.project_root).expanduser() if req.project_root else None,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail=f"Delegation state for thread '{thread_id}' not found")
        return payload

    @router.get("/api/threads/{thread_id}/delegate/wait")
    def get_delegate_wait(thread_id: str):
        thread = thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")

        state = delegation_store.read(thread_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Delegation state for thread '{thread_id}' not found")

        protocol_name = thread.get("protocol")
        protocol = protocol_store.get_protocol(protocol_name) if isinstance(protocol_name, str) else None
        if protocol is None:
            raise HTTPException(status_code=404, detail=f"Protocol '{protocol_name}' not found")

        phase_name = state.current_phase or thread.get("current_phase")
        wait_thread = dict(thread)
        if isinstance(phase_name, str) and phase_name:
            wait_thread["current_phase"] = phase_name
        contributions = thread_store.list_contributions(
            thread_id,
            phase=phase_name if isinstance(phase_name, str) else None,
        )
        plan = describe_next(wait_thread, protocol, contributions)
        resume_token = build_delegation_resume_token(state)
        if state.last_resume_token != resume_token:
            state = state.model_copy(update={"last_resume_token": resume_token})
            delegation_store.write(state)
        return build_delegation_resume_packet(
            thread=thread,
            protocol=protocol,
            state=state,
            valid_outcomes=plan.valid_outcomes,
        )

    @router.post("/api/threads/{thread_id}/delegate/respond")
    def respond_delegate(thread_id: str, req: DelegateRespondRequest):
        thread = thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")

        state = delegation_store.read(thread_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Delegation state for thread '{thread_id}' not found")
        if state.status != "waiting_for_human":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "delegation_not_waiting_for_human",
                    "status": state.status,
                    "required_action": state.required_action,
                },
            )

        expected_resume_token = build_delegation_resume_token(state)
        if req.resume_token is not None and req.resume_token != expected_resume_token:
            raise HTTPException(
                status_code=409,
                detail={"error": "stale_resume_token", "expected": expected_resume_token},
            )

        protocol_name = thread.get("protocol")
        protocol = protocol_store.get_protocol(protocol_name) if isinstance(protocol_name, str) else None
        if protocol is None:
            raise HTTPException(status_code=404, detail=f"Protocol '{protocol_name}' not found")

        phase_cycle_state = phase_cycle_store.read(thread_id)
        phase = _resolve_delegate_phase(thread=thread, protocol=protocol, phase_cycle_state=phase_cycle_state)
        if phase is None:
            raise HTTPException(status_code=409, detail={"error": "phase_not_found"})
        if phase_cycle_state is None:
            phase_cycle_state = phase_cycle_store.start_cycle(
                thread_id=thread_id,
                phase_name=phase.name,
                procedure_steps=[step.action for step in phase.procedure or []],
            )

        plan_thread = dict(thread)
        current_phase_name = state.current_phase or phase_cycle_state.phase_name
        if isinstance(current_phase_name, str) and current_phase_name:
            plan_thread["current_phase"] = current_phase_name
        contributions = thread_store.list_contributions(
            thread_id,
            phase=current_phase_name if isinstance(current_phase_name, str) else None,
        )
        plan = describe_next(plan_thread, protocol, contributions, outcome=req.outcome)
        if plan.error or not plan.passed or plan.suggested_action != "advance_phase" or not plan.next_phase:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": plan.error or "outcome_not_applicable",
                    "passed": plan.passed,
                    "missing": plan.missing,
                    "requested_outcome": plan.requested_outcome,
                    "valid_outcomes": plan.valid_outcomes,
                    "suggested_action": plan.suggested_action,
                },
            )

        next_phase = next((item for item in protocol.phases if item.name == plan.next_phase), None)
        if next_phase is None:
            raise HTTPException(status_code=409, detail={"error": "next_phase_not_found", "next_phase": plan.next_phase})

        updated_thread = thread_store.advance_phase(
            thread_id,
            next_phase=plan.next_phase,
            phase_participants=default_phase_participants(thread, next_phase),
        )
        if updated_thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found or closed")

        transition = advance_phase_cycle(
            thread=plan_thread,
            protocol=protocol,
            current_state=phase_cycle_state,
            outcome=req.outcome,
        )
        next_cycle_state = phase_cycle_store.write(transition.next_state)
        next_phase_name = next_cycle_state.phase_name
        next_phase_def = next((item for item in protocol.phases if item.name == next_phase_name), None)
        if next_phase_def is None:
            raise HTTPException(status_code=409, detail={"error": "phase_not_found", "phase": next_phase_name})

        next_assignment_thread = dict(updated_thread)
        next_assignment_thread["current_phase"] = next_phase_name
        next_contributions = thread_store.list_contributions(thread_id, phase=next_phase_name)
        if next_phase_name == current_phase_name and next_cycle_state.cycle_index > phase_cycle_state.cycle_index:
            next_contributions = []
        next_assignment = build_delegation_assignment(
            thread=next_assignment_thread,
            protocol=protocol,
            phase_cycle_state=next_cycle_state,
            role_bindings=_build_delegate_role_bindings(updated_thread, next_phase_def),
            contributions=next_contributions,
        )
        next_state = _delegation_state_from_assignment(
            thread=updated_thread,
            protocol=protocol,
            thread_id=thread_id,
            phase_cycle_state=next_cycle_state,
            assignment=next_assignment,
        ).model_copy(update={"stop_reason": next_assignment.stop_reason, "last_resume_token": None})

        if next_assignment.status == "running" and next_assignment.fulfillment_mode in {"registered_agent", "managed_agent_subagent"}:
            dispatched, dispatch_violation = _dispatch_delegate_assignment(
                thread_store,
                thread=updated_thread,
                protocol=protocol,
                thread_id=thread_id,
                assignment=next_assignment,
                phase_cycle_state=next_cycle_state,
                routing_source="btwin.delegate.respond",
                human_summary=req.summary,
            )
            if dispatch_violation is not None:
                reason = dispatch_violation.get("error") if isinstance(dispatch_violation, dict) else "dispatch_failed"
                blocked_state = next_state.model_copy(
                    update={"status": "blocked", "reason_blocked": reason or "dispatch_failed", "stop_reason": reason or "dispatch_failed"}
                )
                delegation_store.write(blocked_state)
                raise HTTPException(status_code=409, detail=blocked_state.model_dump(exclude_none=True))

        delegation_store.write(next_state)
        return next_state.model_dump(exclude_none=True)

    @router.post("/api/threads/{thread_id}/contributions")
    async def submit_contribution(thread_id: str, req: ContributionSubmitRequest):
        thread = thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        proto = protocol_store.get_protocol(thread.get("protocol", ""))
        if proto is not None:
            violation = validate_contribution_submission(
                thread=thread,
                protocol=proto,
                actor=req.agent_name,
                phase_name=req.phase,
            )
            if violation is not None:
                raise HTTPException(status_code=409, detail=violation.model_dump())
        contrib = thread_store.submit_contribution(
            thread_id=thread_id,
            agent_name=req.agent_name,
            phase=req.phase,
            content=req.content,
            tldr=req.tldr,
            executor_type=req.executor_type,
            executor_id=req.executor_id,
            subagent_profile=req.subagent_profile,
            parent_executor=req.parent_executor,
            dispatch_id=req.dispatch_id,
        )
        if contrib is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        event_bus.publish(
            SSEEvent(
                type="contribution_submitted",
                resource_id=thread_id,
                metadata={"from_agent": req.agent_name, "tldr": req.tldr, "phase": req.phase},
            )
        )
        return contrib

    @router.get("/api/threads/{thread_id}/contributions")
    def list_contributions(
        thread_id: str,
        phase: str | None = None,
        participant: str | None = None,
        include_history: bool = Query(False, alias="includeHistory"),
    ):
        return thread_store.list_contributions(
            thread_id,
            phase=phase,
            participant=participant,
            include_history=include_history,
        )

    @router.post("/api/threads/{thread_id}/advance-phase")
    async def advance_phase(thread_id: str, req: AdvancePhaseRequest):
        meta = thread_store.get_thread(thread_id)
        if meta is None or meta.get("status") != "active":
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found or closed")

        phase_participants = meta.get("phase_participants", [])
        if not phase_participants:
            raise HTTPException(
                status_code=409,
                detail={"error": "no_phase_participants", "current_phase": meta.get("current_phase")},
            )

        current_phase_name = meta.get("current_phase")
        proto = protocol_store.get_protocol(meta["protocol"])
        if proto and current_phase_name:
            phase_def = next((phase for phase in proto.phases if phase.name == current_phase_name), None)
            if phase_def and ("contribute" in phase_def.actions or "decide" in phase_def.actions) and phase_def.template:
                contributions = thread_store.list_contributions(thread_id, phase=current_phase_name)
                validation = ProtocolValidator.validate_phase(
                    phase_participants=phase_participants,
                    template_sections=phase_def.template,
                    contributions=contributions,
                )
                if not validation.passed:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "phase_requirements_not_met",
                            "current_phase": current_phase_name,
                            "phase_participants": phase_participants,
                            "missing": validation.missing,
                        },
                    )

        next_phase_def = next((phase for phase in proto.phases if phase.name == req.next_phase), None) if proto else None
        old_phase = meta.get("current_phase")
        updated = thread_store.advance_phase(
            thread_id,
            next_phase=req.next_phase,
            phase_participants=default_phase_participants(meta, next_phase_def) if next_phase_def is not None else None,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found or closed")
        event_bus.publish(
            SSEEvent(
                type="thread_updated",
                resource_id=thread_id,
                metadata={"phase": req.next_phase, "old_phase": old_phase},
            )
        )
        return updated

    @router.get("/api/threads/{thread_id}/status")
    def thread_status(thread_id: str, agent: str | None = None):
        if agent is not None:
            status = thread_store.get_agent_status(thread_id, agent)
            if status is None:
                raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' or participant '{agent}' not found")
            return status

        status = thread_store.get_status(thread_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        return status

    @router.post("/api/threads/{thread_id}/spawn-agent")
    async def spawn_agent(thread_id: str, req: SpawnAgentRequest):
        if agent_store is None or agent_store.get_agent(req.agent_name) is None:
            raise HTTPException(status_code=404, detail=f"Agent '{req.agent_name}' not found")

        if thread_store.get_thread(thread_id) is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")

        if agent_runner is None:
            raise HTTPException(status_code=503, detail="Agent runner not configured")

        workspace_root = Path(req.project_root).expanduser() if req.project_root else None
        if hasattr(agent_runner, "get_runtime_session_status"):
            existing_session = agent_runner.get_runtime_session_status(thread_id, req.agent_name)
            if isinstance(existing_session, dict) and hasattr(agent_runner, "attach_or_resume_for_thread"):
                updated = thread_store.join_thread(thread_id, agent_name=req.agent_name)
                if updated is None:
                    raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
                attached = await agent_runner.attach_or_resume_for_thread(
                    thread_id,
                    req.agent_name,
                    bypass_permissions=req.bypass_permissions,
                    workspace_root=workspace_root,
                )
                if attached is None:
                    raise HTTPException(status_code=400, detail=f"Failed to attach agent '{req.agent_name}'")
                event_bus.publish(
                    SSEEvent(
                        type="thread_updated",
                        resource_id=thread_id,
                        metadata={"agent_attached": req.agent_name},
                    )
                )
                return attached

        updated = thread_store.join_thread(thread_id, agent_name=req.agent_name)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")

        if hasattr(agent_runner, "attach_or_resume_for_thread"):
            attached = await agent_runner.attach_or_resume_for_thread(
                thread_id,
                req.agent_name,
                bypass_permissions=req.bypass_permissions,
                workspace_root=workspace_root,
            )
            if attached is None:
                raise HTTPException(status_code=400, detail=f"Failed to attach agent '{req.agent_name}'")
            event_bus.publish(
                SSEEvent(
                    type="thread_updated",
                    resource_id=thread_id,
                    metadata={"agent_attached": req.agent_name},
                )
            )
            return attached

        success = await agent_runner.spawn_for_thread(
            thread_id,
            req.agent_name,
            bypass_permissions=req.bypass_permissions,
            workspace_root=workspace_root,
        )
        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to spawn agent '{req.agent_name}'")

        event_bus.publish(
            SSEEvent(
                type="thread_updated",
                resource_id=thread_id,
                metadata={"agent_spawned": req.agent_name},
            )
        )
        return updated

    @router.post("/api/threads/{thread_id}/recover-agent")
    async def recover_agent(thread_id: str, req: RecoverAgentRequest):
        if agent_store is None or agent_store.get_agent(req.agent_name) is None:
            raise HTTPException(status_code=404, detail=f"Agent '{req.agent_name}' not found")

        if thread_store.get_thread(thread_id) is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")

        if agent_runner is None or not hasattr(agent_runner, "recover_for_thread"):
            raise HTTPException(status_code=503, detail="Agent runner recovery not configured")

        recovered = await agent_runner.recover_for_thread(
            thread_id,
            req.agent_name,
            bypass_permissions=req.bypass_permissions,
            workspace_root=Path(req.project_root).expanduser() if req.project_root else None,
        )
        if recovered is None:
            raise HTTPException(status_code=404, detail=f"Runtime session for '{req.agent_name}' not found")
        if not bool(recovered.get("recovery_started", False)):
            raise HTTPException(status_code=409, detail=recovered)

        event_bus.publish(
            SSEEvent(
                type="thread_updated",
                resource_id=thread_id,
                metadata={"agent_recovered": req.agent_name},
            )
        )
        return recovered

    @router.get("/api/threads/{thread_id}/phase-context")
    def phase_context(thread_id: str):
        builder = PhaseContextBuilder(thread_store, protocol_store)
        ctx = builder.build(thread_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        return ctx

    @router.post("/api/threads/{thread_id}/generate-summary")
    def generate_summary(thread_id: str):
        from btwin_core.llm import LLMClient

        if btwin_factory is None:
            raise HTTPException(status_code=503, detail="BTwin factory not configured")

        twin = btwin_factory()
        llm = LLMClient(twin.config.llm)
        summarizer = ThreadSummarizer(thread_store, llm)
        result = summarizer.generate(thread_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        return result

    @router.post("/api/protocols")
    def create_protocol(req: dict):
        try:
            proto = compile_protocol_definition(req)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        protocol_store.save_protocol(proto)
        return proto.model_dump(exclude_none=True, by_alias=True)

    @router.put("/api/protocols/{name}")
    def update_protocol(name: str, req: dict):
        try:
            proto = compile_protocol_definition(req)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if proto.name != name:
            raise HTTPException(status_code=400, detail="Protocol name in body must match URL")
        protocol_store.save_protocol(proto)
        return proto.model_dump(exclude_none=True, by_alias=True)

    @router.post("/api/protocols/preview")
    def preview_protocol(req: dict):
        try:
            return build_protocol_preview(req)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @router.get("/api/protocols/{name}/preview")
    def preview_saved_protocol(name: str):
        proto = protocol_store.get_protocol(name)
        if proto is None:
            raise HTTPException(status_code=404, detail=f"Protocol '{name}' not found")
        return build_protocol_preview(proto, source={"kind": "store", "name": name})

    @router.delete("/api/protocols/{name}")
    def delete_protocol(name: str):
        if not protocol_store.delete_protocol(name):
            raise HTTPException(status_code=404, detail=f"Protocol '{name}' not found in project scope")
        return {"ok": True, "deleted": name}

    @router.get("/api/agent-runtime-status")
    def agent_runtime_status():
        if agent_runner is None:
            return {"agents": {}}
        if hasattr(agent_runner, "list_runtime_sessions_by_agent"):
            return {"agents": _normalize_runtime_sessions_by_agent(agent_runner.list_runtime_sessions_by_agent())}
        return {"agents": agent_runner.list_active_threads_by_agent()}

    return router
