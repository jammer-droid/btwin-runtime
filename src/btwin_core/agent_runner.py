"""Agent Runner v2 — CLI non-interactive invocation with session resume."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from btwin_core.agent_store import AgentStore
from btwin_core.auth_adapters import ResolvedLaunchAuth, build_auth_adapter
from btwin_core.config import BTwinConfig, load_config
from btwin_core.context_formatter import ContextFormatter
from btwin_core.event_bus import EventBus, SSEEvent
from btwin_core.gateway_client import GatewayLaunchContext, build_gateway_client
from btwin_core.message_router import MessageRouter
from btwin_core.protocol_store import ProtocolStore
from btwin_core.providers import CLIProvider, get_provider, get_provider_runtime_profile
from btwin_core.resource_paths import resolve_bundled_providers_path, resolve_workspace_root
from btwin_core.runtime_logging import RuntimeEventLogger
from btwin_core.session_transcript import normalize_runtime_events
from btwin_core.session_supervisor import (
    RuntimeSession as AgentSession,
    SessionDeliveryResult,
    SessionSupervisor,
)
from btwin_core.session_transports import TransportLaunchContext, build_transport_for_provider
from btwin_core.thread_store import ThreadStore
from btwin_core.prototypes.persistent_sessions.base import PersistentSessionAdapter
from btwin_core.prototypes.persistent_sessions.types import SessionConfig, SessionTurn

logger = logging.getLogger(__name__)

MAX_CHAIN_DEPTH = 5
INVOKE_TIMEOUT = 300  # 5 minutes
PROMPT_MAX_BYTES = 24_000
LIVE_TRANSPORT_IDLE_TIMEOUT = 6.0
LIVE_TRANSPORT_TURN_TIMEOUT = 30.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class InvocationResult:
    ok: bool
    response_text: str = ""
    exit_code: int | None = None
    stderr_summary: str = ""
    timed_out: bool = False
    cli_missing: bool = False
    session_resumed: bool = False
    session_id_captured: str | None = None


@dataclass(frozen=True)
class LaunchResolution:
    provider: CLIProvider
    auth: ResolvedLaunchAuth
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)




class AgentRunner:
    def __init__(
        self,
        thread_store: ThreadStore,
        protocol_store: ProtocolStore,
        agent_store: AgentStore,
        event_bus: EventBus,
        providers_path: Path | None = None,
        config: BTwinConfig | None = None,
        runtime_event_logger: RuntimeEventLogger | None = None,
    ) -> None:
        self._threads = thread_store
        self._protocols = protocol_store
        self._agents = agent_store
        self._event_bus = event_bus
        self._config = self._resolve_runtime_config(config)
        self._runtime_event_logger = runtime_event_logger
        self._providers_config = self._load_providers(providers_path)
        self._provider_cache: dict[str, CLIProvider] = {}
        self._message_router = MessageRouter()
        self._session_supervisor = SessionSupervisor()

        self._sessions = self._session_supervisor.sessions
        self._session_locks = self._session_supervisor.locks
        self._managed_sessions: set[tuple[str, str]] = set()
        self._inbox: dict[tuple[str, str], asyncio.Queue[str]] = {}
        self._active_pids: dict[int, dict] = {}  # pid -> {thread_id, agent_name, started_at}
        self._live_transport_adapters: dict[tuple[str, str], PersistentSessionAdapter] = {}
        self._live_transport_launch_contexts: dict[tuple[str, str], TransportLaunchContext] = {}

    # --- Lifecycle ---

    def start(self) -> None:
        self.cleanup_orphans()
        self._event_bus.subscribe_internal(self._on_event)

    def stop(self) -> None:
        self._event_bus.unsubscribe_internal(self._on_event)

    def cleanup_orphans(self) -> int:
        """Remove dead PIDs from tracking. Returns count of cleaned entries."""
        dead = []
        for pid in list(self._active_pids):
            try:
                os.kill(pid, 0)  # Check if process exists (signal 0 = no signal)
            except ProcessLookupError:
                dead.append(pid)
            except PermissionError:
                pass  # Process exists but we can't signal it
        for pid in dead:
            self._active_pids.pop(pid, None)
        return len(dead)

    # --- Core: invoke ---

    def _get_provider(self, provider_name: str) -> CLIProvider | None:
        """Return a CLIProvider instance for the given provider name."""
        if provider_name in self._provider_cache:
            return self._provider_cache[provider_name]
        provider = get_provider(provider_name)
        if provider is not None:
            self._provider_cache[provider_name] = provider
        return provider

    def _workspace_root(self) -> Path:
        """Return the active workspace root for subprocess working directories."""
        return resolve_workspace_root()

    def _resolve_runtime_config(self, config: BTwinConfig | None) -> BTwinConfig:
        if config is not None:
            return config

        config_path = self._agents.data_dir / "config.yaml"
        if config_path.exists():
            return load_config(config_path)

        return BTwinConfig(data_dir=self._agents.data_dir)

    def _build_thread_snapshot(self, thread_id: str, agent_name: str) -> dict:
        """Build a btwin-owned thread snapshot for prompt rendering."""
        thread = self._threads.get_thread(thread_id)
        if thread is None:
            return {
                "thread_id": thread_id,
                "topic": "",
                "participants": [],
                "current_phase": "none",
                "interaction_mode": "discuss",
                "recent_messages": [],
                "recent_contributions": [],
                "shared_artifacts": [],
                "pending_requests": [],
                "agent_specific_summary": "",
                "agent_name": agent_name,
            }

        messages = self._threads.list_messages(thread_id)
        contributions = self._threads.list_contributions(thread_id)
        snapshot = ContextFormatter.build_thread_snapshot(
            thread=thread,
            messages=messages,
            contributions=contributions,
            agent_name=agent_name,
        )
        snapshot["thread_id"] = thread_id
        return snapshot

    def _emit_session_state(
        self,
        thread_id: str,
        agent_name: str,
        state: str,
        **extra: object,
    ) -> None:
        """Publish a normalized session-state event for UI consumers."""
        metadata = {"agent_name": agent_name, "state": state, **extra}
        self._event_bus.publish(SSEEvent(
            type="agent_session_state",
            resource_id=thread_id,
            metadata=metadata,
        ))

    def _log_runtime_event(
        self,
        event_type: str,
        *,
        thread_id: str,
        agent_name: str,
        provider: str,
        transport_mode: str | None = None,
        level: str = "info",
        message: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if self._runtime_event_logger is None:
            return
        self._runtime_event_logger.log(
            event_type,
            level=level,
            message=message,
            thread_id=thread_id,
            agent_name=agent_name,
            provider=provider,
            transport_mode=transport_mode,
            details=details,
        )

    async def _run_subprocess(
        self,
        cmd: list[str],
        prompt: str,
        provider: CLIProvider,
        thread_id: str,
        agent_name: str,
        *,
        launch_env: dict[str, str] | None = None,
    ) -> InvocationResult:
        """Execute CLI subprocess with streaming output parsing."""
        env = {**os.environ, **(launch_env or provider.env_overrides())}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace_root()),
                env=env,
            )
        except FileNotFoundError:
            self._emit_session_state(thread_id, agent_name, "failed", reason="cli_missing")
            return InvocationResult(ok=False, cli_missing=True)

        if proc.pid:
            self._active_pids[proc.pid] = {
                "thread_id": thread_id,
                "agent_name": agent_name,
                "started_at": _now_iso(),
            }

        self._emit_session_state(thread_id, agent_name, "thinking")

        # Write prompt to stdin
        if proc.stdin:
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()

        # Stream stdout line-by-line
        captured_session_id: str | None = None
        final_text = ""
        observed_text_deltas: list[str] = []
        all_output_lines: list[str] = []
        seen_text_delta = False

        try:
            async with asyncio.timeout(INVOKE_TIMEOUT):
                async for raw_line in proc.stdout:
                    line = raw_line.decode(errors="replace").rstrip("\n")
                    all_output_lines.append(line)

                    event = provider.parse_stream_line(line)
                    if event is None:
                        continue

                    for normalized in normalize_runtime_events([event], provider_name=provider.name):
                        if normalized.kind == "session_started":
                            if normalized.content:
                                captured_session_id = normalized.content
                            continue
                        if normalized.kind == "text_delta":
                            if not seen_text_delta:
                                self._emit_session_state(thread_id, agent_name, "working")
                                self._emit_session_state(thread_id, agent_name, "responding")
                                seen_text_delta = True
                            if normalized.content:
                                observed_text_deltas.append(normalized.content)
                                self._event_bus.publish(SSEEvent(
                                    type="agent_typing",
                                    resource_id=thread_id,
                                    metadata={
                                        "agent_name": agent_name,
                                        "delta": normalized.content,
                                    },
                                ))
                            continue
                        if normalized.kind == "turn_complete" and normalized.content:
                            final_text = normalized.content

                await proc.wait()
        except TimeoutError:
            proc.kill()
            await proc.wait()
            if proc.pid:
                self._active_pids.pop(proc.pid, None)
            self._emit_session_state(thread_id, agent_name, "failed", reason="timeout")
            return InvocationResult(ok=False, timed_out=True)

        stderr_bytes = await proc.stderr.read() if proc.stderr else b""
        stderr_text = stderr_bytes.decode(errors="replace")

        # Fallback: if no final_text from stream events, try full output parse
        if not final_text:
            if observed_text_deltas:
                final_text = "".join(observed_text_deltas)
            else:
                full_output = "\n".join(all_output_lines)
                final_text = provider.parse_final_response(full_output)

        if not captured_session_id:
            full_output = "\n".join(all_output_lines)
            captured_session_id = provider.parse_session_id_from_output(full_output)

        if proc.pid:
            self._active_pids.pop(proc.pid, None)

        if proc.returncode != 0:
            self._emit_session_state(
                thread_id,
                agent_name,
                "failed",
                exit_code=proc.returncode,
            )
            return InvocationResult(
                ok=False,
                response_text=final_text,
                exit_code=proc.returncode,
                stderr_summary=stderr_text[:500],
                session_resumed=bool("--resume" in cmd or "resume" in cmd),
                session_id_captured=captured_session_id,
            )

        self._emit_session_state(thread_id, agent_name, "done")
        self._event_bus.publish(SSEEvent(
            type="agent_typing_done",
            resource_id=thread_id,
            metadata={"agent_name": agent_name},
        ))

        return InvocationResult(
            ok=(proc.returncode == 0),
            response_text=final_text,
            exit_code=proc.returncode,
            stderr_summary=stderr_text[:500],
            session_resumed=bool("--resume" in cmd or "resume" in cmd),
            session_id_captured=captured_session_id,
        )

    async def invoke(self, thread_id: str, agent_name: str, prompt: str) -> InvocationResult:
        session = self._get_or_create_session(thread_id, agent_name)
        if session is None:
            return InvocationResult(ok=False)

        final_result = InvocationResult(ok=False)

        async def _deliver(runtime_session: AgentSession, runtime_prompt: str) -> SessionDeliveryResult:
            nonlocal final_result
            launch = self._resolve_launch_resolution(runtime_session)
            if launch is None:
                logger.warning("Launch resolution failed for %s", runtime_session.provider)
                return SessionDeliveryResult(ok=False)
            self._emit_session_state(thread_id, agent_name, "received")
            runtime_prompt = self._apply_prompt_budget(thread_id, agent_name, runtime_prompt)

            if self._should_use_live_transport(runtime_session):
                result = await self._run_live_transport(
                    runtime_session,
                    runtime_prompt,
                    launch,
                    thread_id=thread_id,
                    agent_name=agent_name,
                )
                final_result = result
                if result.ok:
                    runtime_session.last_transport_error = None
                    if result.session_id_captured:
                        runtime_session.provider_session_id = result.session_id_captured
                    runtime_session.invocation_count += 1
                    runtime_session.last_invoked_at = _now_iso()
                    return SessionDeliveryResult(
                        ok=True,
                        response_text=result.response_text,
                        provider_session_id=result.session_id_captured,
                    )
                if runtime_session.transport_mode == "live_process_transport":
                    runtime_session.last_transport_error = (
                        final_result.stderr_summary
                        or final_result.response_text
                        or runtime_session.last_transport_error
                        or "live transport failed"
                    )
                    self._populate_runtime_session_metadata(runtime_session, launch.metadata)
                    fallback_transport_mode = runtime_session.fallback_mode
                    if (
                        fallback_transport_mode is None
                        and self._config.runtime.persistent_transport_auto_fallback
                    ):
                        fallback_transport_mode = "resume_invocation_transport"
                    if fallback_transport_mode:
                        previous_transport_mode = runtime_session.transport_mode
                        if fallback_transport_mode != "live_process_transport":
                            runtime_session.transport_capability = None
                            runtime_session.continuity_mode = None
                            runtime_session.launch_strategy = None
                        runtime_session.transport_mode = fallback_transport_mode
                        runtime_session.fallback_mode = fallback_transport_mode
                        runtime_session.provider_session_id = None
                        self._log_runtime_event(
                            "runtime_transport_fallback",
                            thread_id=thread_id,
                            agent_name=agent_name,
                            provider=runtime_session.provider,
                            transport_mode=runtime_session.transport_mode,
                            message="runtime transport fell back",
                            details={
                                "previousTransportMode": previous_transport_mode,
                                "nextTransportMode": runtime_session.transport_mode,
                                "lastTransportError": runtime_session.last_transport_error,
                            },
                        )
                        self._emit_session_state(
                            thread_id,
                            agent_name,
                            "fallback",
                            previous_transport_mode=previous_transport_mode,
                            transport_mode=runtime_session.transport_mode,
                        )
                        self._emit_session_state(
                            thread_id,
                            agent_name,
                            "working",
                            transport_mode=runtime_session.transport_mode,
                        )
                    else:
                        self._log_runtime_event(
                            "runtime_session_failed",
                            thread_id=thread_id,
                            agent_name=agent_name,
                            provider=runtime_session.provider,
                            transport_mode=runtime_session.transport_mode,
                            level="warning",
                            message="runtime session failed without fallback",
                            details={"lastTransportError": runtime_session.last_transport_error},
                        )
                        return SessionDeliveryResult(
                            ok=False,
                            response_text=result.response_text,
                        )

            used_resume = bool(runtime_session.provider_session_id)
            preserve_transport_error = bool(runtime_session.last_transport_error)
            cmd = launch.provider.build_command(
                session_id=runtime_session.provider_session_id if used_resume else None,
                bypass_permissions=runtime_session.bypass_permissions,
            )

            result = await self._run_subprocess(
                cmd,
                runtime_prompt,
                launch.provider,
                thread_id,
                agent_name,
                launch_env=launch.env,
            )
            final_result = result

            if result.ok:
                if not preserve_transport_error:
                    runtime_session.last_transport_error = None
                if result.session_id_captured:
                    runtime_session.provider_session_id = result.session_id_captured
                runtime_session.invocation_count += 1
                runtime_session.last_invoked_at = _now_iso()
                self._log_runtime_event(
                    "runtime_session_recovered" if preserve_transport_error else (
                        "runtime_session_reused" if used_resume else "runtime_session_started"
                    ),
                    thread_id=thread_id,
                    agent_name=agent_name,
                    provider=runtime_session.provider,
                    transport_mode=runtime_session.transport_mode,
                    message=(
                        "runtime session recovered after transport issue"
                        if preserve_transport_error
                        else ("runtime session reused" if used_resume else "runtime session started")
                    ),
                    details={"providerSessionId": result.session_id_captured or runtime_session.provider_session_id},
                )
                return SessionDeliveryResult(
                    ok=True,
                    response_text=result.response_text,
                    provider_session_id=result.session_id_captured,
                )

            # Retry with fresh session if resume failed
            if used_resume:
                logger.info("Resume failed for %s/%s — retrying fresh", thread_id, agent_name)
                runtime_session.provider_session_id = None
                fresh_prompt = self._apply_prompt_budget(
                    thread_id, agent_name,
                    self._build_initial_context(thread_id, agent_name),
                )
                fresh_cmd = launch.provider.build_command(
                    session_id=None,
                    bypass_permissions=runtime_session.bypass_permissions,
                )
                result = await self._run_subprocess(
                    fresh_cmd,
                    fresh_prompt,
                    launch.provider,
                    thread_id,
                    agent_name,
                    launch_env=launch.env,
                )
                final_result = result
                if result.ok and result.session_id_captured:
                    runtime_session.provider_session_id = result.session_id_captured
                runtime_session.invocation_count += 1
                runtime_session.last_invoked_at = _now_iso()
                self._log_runtime_event(
                    "runtime_session_recovered" if result.ok else "runtime_session_failed",
                    thread_id=thread_id,
                    agent_name=agent_name,
                    provider=runtime_session.provider,
                    transport_mode=runtime_session.transport_mode,
                    level="warning" if not result.ok else "info",
                    message=(
                        "runtime session recovered with a fresh invocation"
                        if result.ok
                        else "runtime session failed after retrying fresh invocation"
                    ),
                    details={
                        "providerSessionId": result.session_id_captured,
                        "stderrSummary": result.stderr_summary or None,
                    },
                )
                return SessionDeliveryResult(
                    ok=result.ok,
                    response_text=result.response_text,
                    provider_session_id=result.session_id_captured,
                )

            self._log_runtime_event(
                "runtime_session_failed",
                thread_id=thread_id,
                agent_name=agent_name,
                provider=runtime_session.provider,
                transport_mode=runtime_session.transport_mode,
                level="warning",
                message="runtime session failed",
                details={"stderrSummary": result.stderr_summary or None},
            )
            return SessionDeliveryResult(
                ok=False,
                response_text=result.response_text,
            )

        delivery = await self._session_supervisor.deliver_message(
            thread_id,
            agent_name,
            prompt,
            deliver=_deliver,
        )
        session = self._session_supervisor.get_session(thread_id, agent_name)
        final_result.ok = delivery.ok
        final_result.response_text = delivery.response_text
        final_result.session_id_captured = session.provider_session_id if session else None
        return final_result

    # --- Spawn ---

    async def spawn_for_thread(self, thread_id: str, agent_name: str) -> bool:
        """Register agent immediately, invoke in background."""
        key = (thread_id, agent_name)

        # Idempotent guard: if this thread/agent is already managed, do not reschedule.
        if key in self._managed_sessions:
            return True

        # Verify agent exists and create session
        session = self._get_or_create_session(thread_id, agent_name)
        if session is None:
            return False

        # Register BEFORE invoke — messages will be routed to this agent
        self._managed_sessions.add(key)

        # Fire-and-forget: initial invocation in background
        asyncio.create_task(self._background_spawn(thread_id, agent_name))
        return True

    async def _background_spawn(self, thread_id: str, agent_name: str) -> None:
        """Background task: run initial invocation and save response."""
        key = (thread_id, agent_name)
        try:
            self._emit_session_state(thread_id, agent_name, "queued")
            prompt = self._build_initial_context(thread_id, agent_name)
            result = await self.invoke(thread_id, agent_name, prompt)

            if not result.ok:
                logger.warning(
                    "Background spawn failed for %s/%s: exit_code=%s cli_missing=%s timed_out=%s stderr=%s",
                    thread_id,
                    agent_name,
                    result.exit_code,
                    result.cli_missing,
                    result.timed_out,
                    result.stderr_summary,
                )
                self._managed_sessions.discard(key)
                return

            if result.response_text.strip():
                self._save_agent_message(
                    thread_id, agent_name,
                    result.response_text.strip(),
                    chain_depth=1,
                )

            await self._drain_inbox(thread_id, agent_name, chain_depth=1)
        except Exception:
            logger.exception("Background spawn error for %s/%s", thread_id, agent_name)
            self._managed_sessions.discard(key)

    async def _drain_inbox(self, thread_id: str, agent_name: str, chain_depth: int) -> None:
        """Deliver any queued inbox messages as a single batched invocation."""
        key = (thread_id, agent_name)
        q = self._inbox.pop(key, None)
        if q is None:
            return

        prompts: list[str] = []
        while not q.empty():
            prompts.append(q.get_nowait())

        if not prompts:
            return

        # Batch queued messages into one prompt
        batched = "Messages received while you were busy:\n\n" + "\n---\n".join(prompts)
        result = await self.invoke(thread_id, agent_name, batched)
        if result.ok and result.response_text.strip():
            self._save_agent_message(
                thread_id, agent_name,
                result.response_text.strip(),
                chain_depth=chain_depth,
            )

    # --- Event Handling ---

    async def _on_event(self, event: SSEEvent) -> None:
        if event.type == "message_sent":
            await self._handle_message(event)
        elif event.type == "thread_updated":
            await self._handle_phase_change(event)
        elif event.type == "thread_closed":
            self._handle_thread_close(event)

    async def _handle_message(self, event: SSEEvent) -> None:
        thread_id = event.resource_id
        metadata = event.metadata or {}
        from_agent = metadata.get("from_agent", "")
        content = str(metadata.get("content", ""))
        chain_depth = int(metadata.get("chain_depth", 0) or 0)
        message_id = metadata.get("message_id")
        client_message_id = metadata.get("client_message_id")

        if chain_depth >= MAX_CHAIN_DEPTH:
            return

        thread = self._threads.get_thread(thread_id)
        if thread is None:
            return

        managed_agents = {
            agent_name
            for managed_thread_id, agent_name in self._managed_sessions
            if managed_thread_id == thread_id
        }
        decision = self._message_router.route(
            thread=thread,
            envelope={
                "from_agent": from_agent,
                "delivery_mode": metadata.get("delivery_mode", "auto"),
                "target_agents": metadata.get("target_agents", []),
                "content": content,
            },
            managed_agents=managed_agents,
            snapshot=self._build_thread_snapshot(thread_id, ""),
        )
        self._event_bus.publish(SSEEvent(
            type="message_routed",
            resource_id=thread_id,
            metadata={
                "from_agent": from_agent,
                "mode": decision.mode,
                "targets": decision.targets,
                "source": decision.source,
                "reason": decision.reason,
                "message_id": message_id,
                "client_message_id": client_message_id,
            },
        ))
        if decision.mode == "ignore":
            return

        for agent_name in decision.targets:
            key = (thread_id, agent_name)

            self._emit_session_state(thread_id, agent_name, "queued")

            relay = ContextFormatter.format_message_relay(
                from_agent=from_agent,
                content=content,
                thread_id=thread_id,
                phase_name=thread.get("current_phase"),
            )

            lock = self._session_locks.get(key)
            if lock and lock.locked():
                # Agent busy — queue the message
                if key not in self._inbox:
                    self._inbox[key] = asyncio.Queue()
                await self._inbox[key].put(relay)
                continue

            snapshot = self._build_thread_snapshot(thread_id, agent_name)
            prompt = ContextFormatter.render_oneshot_prompt(
                snapshot=snapshot,
                ask=relay,
            )
            result = await self.invoke(thread_id, agent_name, prompt)
            if result.ok and result.response_text.strip():
                self._save_agent_message(
                    thread_id, agent_name,
                    result.response_text.strip(),
                    chain_depth=chain_depth + 1,
                )

            # Drain inbox after invoke completes
            await self._drain_inbox(thread_id, agent_name, chain_depth + 1)

    async def _handle_phase_change(self, event: SSEEvent) -> None:
        thread_id = event.resource_id
        metadata = event.metadata or {}
        new_phase_name = metadata.get("phase")
        old_phase = str(metadata.get("old_phase", "unknown"))
        if not new_phase_name:
            return

        thread = self._threads.get_thread(thread_id)
        if thread is None:
            return

        protocol = self._protocols.get_protocol(thread["protocol"])
        if protocol is None:
            return

        phase_def = next((p for p in protocol.phases if p.name == new_phase_name), None)
        if phase_def is None:
            return

        text = ContextFormatter.format_phase_transition(
            old_phase=old_phase,
            new_phase_def=phase_def.model_dump(),
        )

        for key in list(self._managed_sessions):
            if key[0] != thread_id:
                continue
            result = await self.invoke(thread_id, key[1], text)
            if result.ok and result.response_text.strip():
                self._save_agent_message(
                    thread_id,
                    key[1],
                    result.response_text.strip(),
                    chain_depth=0,
                )

    def _handle_thread_close(self, event: SSEEvent) -> None:
        thread_id = event.resource_id
        keys_to_remove = {k for k in self._managed_sessions if k[0] == thread_id}
        self._managed_sessions -= keys_to_remove
        for key in keys_to_remove:
            self._close_live_transport_adapter(key)
        self._session_supervisor.close_thread_sessions_nowait(thread_id)

    # --- Query ---

    def list_active_threads_by_agent(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for thread_id, agent_name in self._managed_sessions:
            result.setdefault(agent_name, []).append(thread_id)
        return result

    def list_runtime_sessions_by_agent(self) -> dict[str, list[dict[str, object]]]:
        result: dict[str, list[dict[str, object]]] = {}
        for session in self._sessions.values():
            payload: dict[str, object] = {
                "thread_id": session.thread_id,
                "provider": session.provider,
                "transport_mode": session.transport_mode,
                "fallback_mode": session.fallback_mode,
                "status": str(session.status),
                "provider_session_id": session.provider_session_id,
                "last_activity_at": session.last_activity_at,
                "last_transport_error": session.last_transport_error,
            }
            payload["fallback_transport_involved"] = (
                isinstance(session.fallback_mode, str)
                and bool(session.fallback_mode)
                and session.transport_mode == session.fallback_mode
            )
            if session.auth_mode is not None:
                payload["auth_mode"] = session.auth_mode
            if session.gateway_mode is not None:
                payload["gateway_mode"] = session.gateway_mode
            if session.gateway_route is not None:
                payload["gateway_route"] = session.gateway_route
            if session.transport_capability is not None:
                payload["transport_capability"] = session.transport_capability
            if session.continuity_mode is not None:
                payload["continuity_mode"] = session.continuity_mode
            if session.launch_strategy is not None:
                payload["launch_strategy"] = session.launch_strategy
            if session.last_transport_error is not None:
                payload["last_transport_error"] = session.last_transport_error
            result.setdefault(session.agent_name, []).append(payload)
        return result

    def _save_agent_message(self, thread_id: str, agent_name: str, content: str, chain_depth: int) -> None:
        tldr = content[:100].replace("\n", " ")
        if len(content) > 100:
            tldr = tldr[:97] + "..."
        saved_message = self._threads.send_message(
            thread_id=thread_id,
            from_agent=agent_name,
            content=content,
            tldr=tldr,
            msg_type="message",
        )
        if saved_message is None:
            return
        self._event_bus.publish(SSEEvent(
            type="message_sent",
            resource_id=thread_id,
            metadata={
                "message_id": saved_message["message_id"],
                "from_agent": agent_name,
                "content": content,
                "chain_depth": chain_depth,
            },
        ))

    def _should_use_live_transport(self, session: AgentSession) -> bool:
        if session.transport_mode != "live_process_transport":
            return False
        transport = build_transport_for_provider(
            session.provider,
            runtime_config=self._config.runtime,
        )
        return transport.mode == "live_process_transport"

    async def _run_live_transport(
        self,
        session: AgentSession,
        prompt: str,
        launch: LaunchResolution,
        *,
        thread_id: str,
        agent_name: str,
    ) -> InvocationResult:
        key = (thread_id, agent_name)
        launch_context = self._build_transport_launch_context(session, launch)
        transport = build_transport_for_provider(
            session.provider,
            runtime_config=self._config.runtime,
        )
        refreshing_live_adapter = self._live_transport_requires_fresh_start(session, launch_context)
        adapter = self._live_transport_adapters.get(key)
        reused_existing_adapter = adapter is not None and not refreshing_live_adapter
        try:
            if adapter is not None and not refreshing_live_adapter and transport.requires_health_check_before_reuse:
                try:
                    health = await adapter.health_check()
                except Exception:
                    health = None
                if health is None or not health.ok:
                    refreshing_live_adapter = True
                    await self._close_live_transport_adapter_async(key)
                    adapter = None
            if adapter is not None and refreshing_live_adapter:
                await self._close_live_transport_adapter_async(key)
                adapter = None
            if adapter is None:
                try:
                    adapter = transport.build_adapter(launch_context)
                except TypeError:
                    adapter = transport.build_adapter()
                if adapter is None:
                    return InvocationResult(ok=False, stderr_summary="live transport unavailable")
                build_session_config = getattr(transport, "build_session_config", None)
                if callable(build_session_config):
                    resume_session_id = None if refreshing_live_adapter else session.provider_session_id
                    session_config = build_session_config(
                        launch_context,
                        resume_session_id=resume_session_id,
                    )
                else:
                    session_config = None
                if session_config is None:
                    start_metadata = dict(launch.metadata)
                    if session.provider_session_id and not refreshing_live_adapter:
                        start_metadata["resume_session_id"] = session.provider_session_id
                    session_config = SessionConfig(
                        options={"env": dict(launch.env)} if launch.env else {},
                        metadata=start_metadata,
                    )
                start_result = await adapter.start(
                    session_config
                )
                if start_result.metadata.get("ok") is False:
                    message = start_result.metadata.get("message")
                    raise RuntimeError(str(message or "live transport start failed"))
                self._live_transport_adapters[key] = adapter
                self._live_transport_launch_contexts[key] = launch_context
                self._populate_runtime_session_metadata(session, launch.metadata)
                start_metadata = {
                    key: value
                    for key, value in start_result.metadata.items()
                    if isinstance(value, str) and value
                }
                self._populate_runtime_session_metadata(session, start_metadata)
                if start_result.session_id:
                    session.provider_session_id = start_result.session_id
                self._log_runtime_event(
                    "runtime_session_started",
                    thread_id=thread_id,
                    agent_name=agent_name,
                    provider=session.provider,
                    transport_mode=session.transport_mode,
                    message="runtime session started",
                    details={
                        "providerSessionId": start_result.session_id or session.provider_session_id,
                        "refreshedAdapter": refreshing_live_adapter,
                    },
                )
            elif reused_existing_adapter:
                self._log_runtime_event(
                    "runtime_session_reused",
                    thread_id=thread_id,
                    agent_name=agent_name,
                    provider=session.provider,
                    transport_mode=session.transport_mode,
                    message="runtime session reused",
                    details={"providerSessionId": session.provider_session_id},
                )

            self._emit_session_state(
                thread_id,
                agent_name,
                "thinking",
                transport_mode=session.transport_mode,
            )
            await adapter.send_turn(
                SessionTurn(
                    content=prompt,
                    metadata={"turn_index": session.invocation_count + 1},
                )
            )

            observed_text_deltas: list[str] = []
            final_text = ""
            captured_session_id = session.provider_session_id
            seen_text_delta = False
            turn_complete_seen = False
            active_turn_id: str | None = None
            event_iterator = adapter.read_events().__aiter__()
            loop = asyncio.get_running_loop()
            turn_deadline = loop.time() + LIVE_TRANSPORT_TURN_TIMEOUT
            idle_deadline = loop.time() + LIVE_TRANSPORT_IDLE_TIMEOUT

            while True:
                now = loop.time()
                timeout_seconds = max(turn_deadline - now, 0.0)
                if idle_deadline is not None:
                    timeout_seconds = min(timeout_seconds, max(idle_deadline - now, 0.0))
                if timeout_seconds <= 0:
                    if idle_deadline is not None and now >= idle_deadline:
                        raise TimeoutError(
                            f"live transport timed out after {LIVE_TRANSPORT_IDLE_TIMEOUT:.2f}s of inactivity"
                        )
                    raise TimeoutError(
                        f"live transport timed out after {LIVE_TRANSPORT_TURN_TIMEOUT:.2f}s"
                    )
                try:
                    event = await asyncio.wait_for(anext(event_iterator), timeout=timeout_seconds)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    now = loop.time()
                    if idle_deadline is not None and now >= idle_deadline:
                        raise TimeoutError(
                            f"live transport timed out after {LIVE_TRANSPORT_IDLE_TIMEOUT:.2f}s of inactivity"
                        ) from exc
                    raise TimeoutError(
                        f"live transport timed out after {LIVE_TRANSPORT_TURN_TIMEOUT:.2f}s"
                    ) from exc

                idle_deadline = loop.time() + LIVE_TRANSPORT_IDLE_TIMEOUT
                if event.kind == "turn_started" and isinstance(event.content, str) and event.content:
                    active_turn_id = event.content
                for normalized in normalize_runtime_events([event], provider_name=session.provider):
                    if normalized.kind == "session_started":
                        if normalized.content:
                            captured_session_id = normalized.content
                        continue
                    if normalized.kind == "text_delta":
                        if not seen_text_delta:
                            self._emit_session_state(
                                thread_id,
                                agent_name,
                                "working",
                                transport_mode=session.transport_mode,
                            )
                            self._emit_session_state(thread_id, agent_name, "responding")
                            seen_text_delta = True
                        if normalized.content:
                            observed_text_deltas.append(normalized.content)
                            self._event_bus.publish(SSEEvent(
                                type="agent_typing",
                                resource_id=thread_id,
                                metadata={
                                    "agent_name": agent_name,
                                    "delta": normalized.content,
                                },
                            ))
                        continue
                    if normalized.kind == "turn_complete":
                        if session.provider == "codex" and active_turn_id is None:
                            continue
                        if (
                            active_turn_id
                            and normalized.content
                            and normalized.content != active_turn_id
                        ):
                            continue
                        if normalized.content and not observed_text_deltas:
                            final_text = normalized.content
                        turn_complete_seen = True
                        break
                if turn_complete_seen:
                    break

            if not turn_complete_seen:
                raise RuntimeError("live transport ended before turn completed")

            if not final_text and observed_text_deltas:
                final_text = "".join(observed_text_deltas)

            session.last_transport_error = None
            self._emit_session_state(thread_id, agent_name, "done")
            self._event_bus.publish(SSEEvent(
                type="agent_typing_done",
                resource_id=thread_id,
                metadata={"agent_name": agent_name},
            ))
            return InvocationResult(
                ok=True,
                response_text=final_text,
                session_id_captured=captured_session_id,
            )
        except Exception as exc:  # noqa: BLE001
            await self._close_live_transport_adapter_async(key)
            self._emit_session_state(
                thread_id,
                agent_name,
                "failed",
                reason="live_transport_failed",
            )
            return InvocationResult(
                ok=False,
                stderr_summary=str(exc),
                session_id_captured=session.provider_session_id,
            )

    def _close_live_transport_adapter(self, key: tuple[str, str]) -> None:
        adapter = self._detach_live_transport_adapter(key)
        if adapter is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(adapter.close())

    def _detach_live_transport_adapter(self, key: tuple[str, str]) -> PersistentSessionAdapter | None:
        adapter = self._live_transport_adapters.pop(key, None)
        self._live_transport_launch_contexts.pop(key, None)
        return adapter

    async def _close_live_transport_adapter_async(self, key: tuple[str, str]) -> None:
        adapter = self._detach_live_transport_adapter(key)
        if adapter is None:
            return
        try:
            await adapter.close()
        except Exception:  # noqa: BLE001
            return

    # --- Internal Helpers ---

    def _session_launch_metadata(
        self,
        *,
        auth: ResolvedLaunchAuth,
    ) -> dict[str, str]:
        metadata = {
            "auth_mode": auth.metadata.get("auth_mode") or auth.mode,
        }
        if auth.token_ref:
            metadata["token_ref"] = auth.token_ref
        return metadata

    def _prepare_gateway_launch(
        self,
        *,
        provider_name: str,
        auth: ResolvedLaunchAuth,
        env: dict[str, str] | None = None,
        command: list[str] | None = None,
    ):
        return build_gateway_client(self._config).prepare_launch(
            GatewayLaunchContext(
                provider_name=provider_name,
                command=command or [],
                env=env or {},
                metadata=self._session_launch_metadata(auth=auth),
            )
        )

    def _build_transport_launch_context(
        self,
        session: AgentSession,
        launch: LaunchResolution,
    ) -> TransportLaunchContext:
        auth_mode = launch.metadata.get("auth_mode") or launch.auth.metadata.get("auth_mode") or launch.auth.mode
        token_ref = launch.metadata.get("token_ref")
        if not isinstance(token_ref, str) or not token_ref:
            legacy_token_ref = launch.metadata.get("auth_token_ref")
            if isinstance(legacy_token_ref, str) and legacy_token_ref:
                token_ref = legacy_token_ref
            elif launch.auth.token_ref:
                token_ref = launch.auth.token_ref
            else:
                token_ref = None
        gateway_metadata = {
            key: value
            for key, value in launch.metadata.items()
            if key in {"gateway_mode", "gateway_route", "gateway_base_url"}
            and isinstance(value, str)
        }
        return TransportLaunchContext(
            provider_name=launch.provider.name,
            transport_mode=session.transport_mode,
            auth_mode=auth_mode if isinstance(auth_mode, str) and auth_mode else None,
            token_ref=token_ref if isinstance(token_ref, str) and token_ref else None,
            gateway_metadata=gateway_metadata,
            env=dict(launch.env),
        )

    def _resolve_session_metadata(self, session: AgentSession) -> dict[str, str] | None:
        agent = self._agents.get_agent(session.agent_name)
        if agent is None:
            return None
        profile = get_provider_runtime_profile(session.provider)
        auth = build_auth_adapter(session.provider).resolve(
            agent=agent,
            runtime_config=self._config.runtime,
        )
        gateway = self._prepare_gateway_launch(
            provider_name=session.provider,
            auth=auth,
        )
        metadata = {
            key: value
            for key, value in gateway.metadata.items()
            if isinstance(value, str)
        }
        if profile is not None and session.transport_mode == "live_process_transport":
            metadata["transport_capability"] = profile.transport_capability
            metadata["continuity_mode"] = profile.continuity_mode
        self._populate_runtime_session_metadata(session, metadata)
        return metadata

    def _resolve_launch_resolution(self, session: AgentSession) -> LaunchResolution | None:
        agent = self._agents.get_agent(session.agent_name)
        if agent is None:
            return None
        provider = self._get_provider(session.provider)
        if provider is None:
            return None
        profile = get_provider_runtime_profile(session.provider)

        auth = build_auth_adapter(session.provider).resolve(
            agent=agent,
            runtime_config=self._config.runtime,
        )
        gateway = self._prepare_gateway_launch(
            provider_name=session.provider,
            auth=auth,
            env=provider.env_overrides(auth),
        )
        metadata = {
            key: value
            for key, value in gateway.metadata.items()
            if isinstance(value, str)
        }
        if profile is not None and session.transport_mode == "live_process_transport":
            metadata["transport_capability"] = profile.transport_capability
            metadata["continuity_mode"] = profile.continuity_mode
        launch = LaunchResolution(
            provider=provider,
            auth=auth,
            env=dict(gateway.env),
            metadata=metadata,
        )
        launch_context = self._build_transport_launch_context(session, launch)
        if not self._live_transport_requires_fresh_start(session, launch_context):
            self._populate_runtime_session_metadata(session, metadata)
        return launch

    def _get_or_create_session(self, thread_id: str, agent_name: str) -> AgentSession | None:
        key = (thread_id, agent_name)
        if key in self._sessions:
            return self._sessions[key]

        agent = self._agents.get_agent(agent_name)
        if agent is None:
            return None

        provider = self._resolve_provider(agent)
        if provider is None:
            return None
        transport = build_transport_for_provider(provider, runtime_config=self._config.runtime)

        session = self._session_supervisor.ensure_session_nowait(
            thread_id=thread_id,
            agent_name=agent_name,
            provider=provider,
            transport_mode=transport.mode,
            bypass_permissions=bool(agent.get("bypass_permissions", False)),
        )
        session.fallback_mode = transport.fallback_mode
        self._resolve_session_metadata(session)
        return session

    def _populate_runtime_session_metadata(
        self,
        session: AgentSession,
        metadata: dict[str, str],
    ) -> None:
        auth_mode = metadata.get("auth_mode")
        if isinstance(auth_mode, str) and auth_mode:
            session.auth_mode = auth_mode

        gateway_mode = metadata.get("gateway_mode")
        if isinstance(gateway_mode, str) and gateway_mode:
            session.gateway_mode = gateway_mode

        gateway_route = metadata.get("gateway_route")
        if isinstance(gateway_route, str) and gateway_route:
            session.gateway_route = gateway_route

        transport_capability = metadata.get("transport_capability") or metadata.get("capability")
        if isinstance(transport_capability, str) and transport_capability:
            session.transport_capability = transport_capability

        continuity_mode = metadata.get("continuity_mode")
        if isinstance(continuity_mode, str) and continuity_mode:
            session.continuity_mode = continuity_mode

        launch_strategy = metadata.get("launch_strategy")
        if isinstance(launch_strategy, str) and launch_strategy:
            session.launch_strategy = launch_strategy

        last_transport_error = metadata.get("last_transport_error")
        if isinstance(last_transport_error, str) and last_transport_error:
            session.last_transport_error = last_transport_error

    def _live_transport_requires_fresh_start(
        self,
        session: AgentSession,
        launch_context: TransportLaunchContext,
    ) -> bool:
        if session.transport_mode != "live_process_transport":
            return False
        key = (session.thread_id, session.agent_name)
        if key not in self._live_transport_adapters:
            return False
        previous_launch_context = self._live_transport_launch_contexts.get(key)
        return previous_launch_context != launch_context

    def _resolve_provider(self, agent: dict) -> str | None:
        model_id = agent.get("model", "")
        cli_config = agent.get("cli_config") or {}
        explicit_provider = cli_config.get("provider")
        if explicit_provider:
            profile = get_provider_runtime_profile(str(explicit_provider))
            canonical_name = (
                profile.canonical_name
                if profile is not None
                else str(explicit_provider)
            )
            return canonical_name if get_provider(canonical_name) is not None else None

        for provider in self._providers_config:
            for model in provider.get("models", []):
                if model["id"] == model_id:
                    provider_id = provider["id"]
                    if get_provider(provider_id) is not None:
                        return provider_id

                    cli_name = str(provider.get("cli", "")).lower()
                    if "claude" in cli_name:
                        return "claude-code"
                    if "codex" in cli_name:
                        return "codex"
        return None

    def _build_initial_context(self, thread_id: str, agent_name: str) -> str:
        thread = self._threads.get_thread(thread_id)
        if thread is None:
            return ""

        protocol = self._protocols.get_protocol(thread["protocol"])
        proto_dict = protocol.model_dump() if protocol else {"name": thread["protocol"], "phases": []}
        messages = self._threads.list_messages(thread_id)
        contributions = self._threads.list_contributions(thread_id)

        return ContextFormatter.format_initial_context(
            thread=thread,
            protocol=proto_dict,
            messages=messages,
            contributions=contributions,
            agent_name=agent_name,
        )

    def _apply_prompt_budget(self, thread_id: str, agent_name: str, prompt: str) -> str:
        if len(prompt.encode("utf-8")) <= PROMPT_MAX_BYTES:
            return prompt

        thread = self._threads.get_thread(thread_id)
        if thread is None:
            return prompt.encode("utf-8")[:PROMPT_MAX_BYTES].decode("utf-8", errors="replace")

        protocol = self._protocols.get_protocol(thread["protocol"])
        proto_dict = protocol.model_dump() if protocol else {"name": thread["protocol"], "phases": []}
        messages = self._threads.list_messages(thread_id)
        contributions = self._threads.list_contributions(thread_id)

        rebuilt = ContextFormatter.format_initial_context(
            thread=thread,
            protocol=proto_dict,
            messages=messages[-8:],
            contributions=contributions[-6:],
        )
        if len(rebuilt.encode("utf-8")) <= PROMPT_MAX_BYTES:
            return rebuilt

        encoded = rebuilt.encode("utf-8")[:PROMPT_MAX_BYTES]
        return encoded.decode("utf-8", errors="replace")

    @staticmethod
    def _load_providers(path: Path | None) -> list[dict]:
        if path and path.exists():
            return json.loads(path.read_text(encoding="utf-8")).get("providers", [])
        global_path = resolve_bundled_providers_path()
        if global_path is not None:
            return json.loads(global_path.read_text(encoding="utf-8")).get("providers", [])
        return []
