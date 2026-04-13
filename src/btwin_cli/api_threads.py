"""HTTP API for thread collaboration — REST + EventBus integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from btwin_core.event_bus import EventBus, SSEEvent
from btwin_core.locale_settings import LocaleSettingsStore
from btwin_core.phase_context import PhaseContextBuilder
from btwin_core.protocol_store import ProtocolStore
from btwin_core.protocol_validator import ProtocolValidator
from btwin_core.thread_store import ThreadStore
from btwin_core.thread_summarizer import ThreadSummarizer

logger = logging.getLogger(__name__)

_RUNTIME_SESSION_FIELDS = (
    "thread_id",
    "provider",
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


class AdvancePhaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    next_phase: str = Field(alias="nextPhase")


class SpawnAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    agent_name: str = Field(alias="agentName")


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

    @router.get("/api/protocols")
    def list_protocols():
        return protocol_store.list_protocols()

    @router.get("/api/protocols/{name}")
    def get_protocol(name: str):
        proto = protocol_store.get_protocol(name)
        if proto is None:
            raise HTTPException(status_code=404, detail=f"Protocol '{name}' not found")
        return proto.model_dump()

    @router.post("/api/threads")
    async def create_thread(req: ThreadCreateRequest):
        proto = protocol_store.get_protocol(req.protocol)
        if proto is None:
            raise HTTPException(status_code=404, detail=f"Protocol '{req.protocol}' not found")
        initial_phase = proto.phases[0].name if proto.phases else None

        thread = thread_store.create_thread(
            topic=req.topic,
            protocol=req.protocol,
            participants=req.participants,
            initial_phase=initial_phase,
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
        closed = thread_store.close_thread(thread_id, summary=req.summary, decision=req.decision)
        if closed is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")

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
                                result_record_id = frontmatter.get("record_id")
                                if result_record_id:
                                    twin.update_entry(
                                        record_id=result_record_id,
                                        related_records=[f"thread:{thread_id}"],
                                    )
            except Exception:
                logger.warning("Failed to create thread result entry for %s", thread_id, exc_info=True)

        event_bus.publish(
            SSEEvent(
                type="thread_closed",
                resource_id=thread_id,
                metadata={"summary": req.summary[:100]},
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
            if agent_runner is not None:
                active_threads = agent_runner.list_active_threads_by_agent()
                inactive_targets = sorted(
                    target for target in req.target_agents if thread_id not in active_threads.get(target, [])
                )
                if inactive_targets:
                    raise HTTPException(
                        status_code=409,
                        detail=f"target agents are not active in this thread: {', '.join(inactive_targets)}",
                    )

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

    @router.post("/api/threads/{thread_id}/contributions")
    async def submit_contribution(thread_id: str, req: ContributionSubmitRequest):
        contrib = thread_store.submit_contribution(
            thread_id=thread_id,
            agent_name=req.agent_name,
            phase=req.phase,
            content=req.content,
            tldr=req.tldr,
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
    def list_contributions(thread_id: str, phase: str | None = None, participant: str | None = None):
        return thread_store.list_contributions(thread_id, phase=phase, participant=participant)

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

        old_phase = meta.get("current_phase")
        updated = thread_store.advance_phase(thread_id, next_phase=req.next_phase)
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
    def thread_status(thread_id: str):
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

        updated = thread_store.join_thread(thread_id, agent_name=req.agent_name)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")

        success = await agent_runner.spawn_for_thread(thread_id, req.agent_name)
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
        from btwin_core.protocol_store import Protocol

        try:
            proto = Protocol.model_validate(req)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        protocol_store.save_protocol(proto)
        return proto.model_dump(exclude_none=True)

    @router.put("/api/protocols/{name}")
    def update_protocol(name: str, req: dict):
        from btwin_core.protocol_store import Protocol

        try:
            proto = Protocol.model_validate(req)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if proto.name != name:
            raise HTTPException(status_code=400, detail="Protocol name in body must match URL")
        protocol_store.save_protocol(proto)
        return proto.model_dump(exclude_none=True)

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
