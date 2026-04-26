from pathlib import Path

import pytest

from btwin_core.agent_runner import AgentRunner, InvocationResult, RuntimeOutput
from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig
from btwin_core.delegation_state import DelegationState
from btwin_core.delegation_store import DelegationStore
from btwin_core.event_bus import EventBus, SSEEvent
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_store import PhaseCycleStore
from btwin_core.protocol_store import ProtocolStore, compile_protocol_definition
from btwin_core.resource_usage_telemetry import ResourceUsageTelemetryStore
from btwin_core.thread_store import ThreadStore


def _build_runner(data_dir: Path) -> tuple[AgentRunner, ThreadStore, ProtocolStore, DelegationStore, PhaseCycleStore]:
    thread_store = ThreadStore(data_dir / "threads")
    protocol_store = ProtocolStore(data_dir / "protocols")
    runner = AgentRunner(
        thread_store,
        protocol_store,
        AgentStore(data_dir),
        EventBus(),
        config=BTwinConfig(data_dir=data_dir),
    )
    return (
        runner,
        thread_store,
        protocol_store,
        DelegationStore(data_dir),
        PhaseCycleStore(data_dir),
    )


def _seed_running_delegation(
    delegation_store: DelegationStore,
    *,
    thread_id: str,
    resolved_agent: str = "alice",
    current_phase: str = "review",
) -> None:
    delegation_store.write(
        DelegationState(
            thread_id=thread_id,
            status="running",
            loop_iteration=1,
            current_phase=current_phase,
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent=resolved_agent,
            required_action="submit_contribution",
            expected_output=f"{current_phase} contribution",
        )
    )


def test_helper_result_advances_delegation_and_dispatches_next_work(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, protocol_store, delegation_store, phase_cycle_store = _build_runner(data_dir)

    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-followup",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Review"}],
                    },
                    {
                        "name": "followup",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Follow Up"}],
                    },
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Delegate followup thread",
        protocol="delegate-followup",
        participants=["alice"],
        initial_phase="review",
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    _seed_running_delegation(delegation_store, thread_id=thread["thread_id"])

    runner._persist_invocation_outputs(
        thread["thread_id"],
        "alice",
        InvocationResult(
            ok=True,
            outputs=(
                RuntimeOutput(
                    content="## completed\nReview is complete.\n",
                    phase="final_answer",
                    state_affecting=True,
                ),
            ),
        ),
        chain_depth=1,
    )

    state = delegation_store.read(thread["thread_id"])
    assert state is not None
    assert state.status == "running"
    assert state.current_phase == "followup"
    assert state.loop_iteration == 2
    assert state.resolved_agent == "alice"

    updated_thread = thread_store.get_thread(thread["thread_id"])
    assert updated_thread is not None
    assert updated_thread["current_phase"] == "followup"

    review_contributions = thread_store.list_contributions(thread["thread_id"], phase="review")
    assert len(review_contributions) == 1
    assert review_contributions[0]["agent"] == "alice"
    assert review_contributions[0]["_content"] == "## completed\nReview is complete."

    delegation_messages = [
        message
        for message in thread_store.list_messages(thread["thread_id"])
        if message.get("msg_type") == "delegation"
    ]
    assert len(delegation_messages) == 1
    assert delegation_messages[0]["from"] == "btwin"
    assert delegation_messages[0]["target_agents"] == ["alice"]
    assert delegation_messages[0]["message_phase"] == "followup"


def test_agent_runner_records_provider_token_usage(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, _protocol_store, _delegation_store, _phase_cycle_store = _build_runner(data_dir)
    thread = thread_store.create_thread(
        topic="Resource usage thread",
        protocol="code-review",
        participants=["alice"],
        initial_phase="analysis",
    )

    runner._record_resource_usage(
        thread_id=thread["thread_id"],
        agent_name="alice",
        prompt="## Context Pack\ncontrol\n\n## Current Ask\nDo the work.",
        response_text="Done.",
        truncated=False,
        provider_usage={
            "provider": "codex",
            "provider_thread_id": "codex-thread-1",
            "provider_turn_id": "turn-1",
            "token_usage": {
                "last": {
                    "inputTokens": 100,
                    "cachedInputTokens": 40,
                    "outputTokens": 20,
                    "reasoningOutputTokens": 5,
                    "totalTokens": 120,
                }
            },
        },
    )

    rows = ResourceUsageTelemetryStore(data_dir).tail(limit=10, thread_id=thread["thread_id"])

    assert len(rows) == 1
    assert rows[0]["event_type"] == "resource.provider_token_usage"
    assert rows[0]["agent_name"] == "alice"
    assert rows[0]["runtime_session_id"] == f"{thread['thread_id']}:alice"
    assert rows[0]["btwin_thread_id"] == thread["thread_id"]
    assert rows[0]["phase"] == "analysis"
    assert rows[0]["prompt_source"] == "context_pack"
    assert rows[0]["actual_total_tokens"] == 120
    assert rows[0]["actual_uncached_input_tokens"] == 60
    assert "context_pack" in rows[0]["context_sections"]


def test_helper_result_waits_for_human_when_outcome_is_ambiguous(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, protocol_store, delegation_store, phase_cycle_store = _build_runner(data_dir)

    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-outcome",
                "outcome_policies": [
                    {
                        "name": "review-outcomes",
                        "emitters": ["reviewer"],
                        "actions": ["decide"],
                        "outcomes": ["retry", "accept"],
                    }
                ],
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Review"}],
                        "outcome_policy": "review-outcomes",
                    }
                ],
                "outcomes": ["retry", "accept"],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Delegate outcome thread",
        protocol="delegate-outcome",
        participants=["alice"],
        initial_phase="review",
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    _seed_running_delegation(delegation_store, thread_id=thread["thread_id"])

    runner._persist_invocation_outputs(
        thread["thread_id"],
        "alice",
        InvocationResult(
            ok=True,
            outputs=(
                RuntimeOutput(
                    content="## completed\nNeeds another pass.\n",
                    phase="final_answer",
                    state_affecting=True,
                ),
            ),
        ),
        chain_depth=1,
    )

    state = delegation_store.read(thread["thread_id"])
    assert state is not None
    assert state.status == "waiting_for_human"
    assert state.current_phase == "review"
    assert state.loop_iteration == 1
    assert state.required_action == "record_outcome"
    assert state.expected_output is not None
    assert "retry" in state.expected_output
    assert "accept" in state.expected_output

    delegation_messages = [
        message
        for message in thread_store.list_messages(thread["thread_id"])
        if message.get("msg_type") == "delegation"
    ]
    assert delegation_messages == []


def test_duplicate_result_message_id_is_not_reprocessed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, protocol_store, delegation_store, phase_cycle_store = _build_runner(data_dir)

    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-followup",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Review"}],
                    },
                    {
                        "name": "followup",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Follow Up"}],
                    },
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Duplicate result thread",
        protocol="delegate-followup",
        participants=["alice"],
        initial_phase="review",
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    _seed_running_delegation(delegation_store, thread_id=thread["thread_id"])

    saved_message = runner._save_agent_message(
        thread["thread_id"],
        "alice",
        "## completed\nReview is complete.\n",
        1,
        message_phase="final_answer",
        state_affecting=True,
    )

    runner._maybe_continue_delegation_from_saved_message(
        thread["thread_id"],
        "alice",
        saved_message,
    )
    runner._maybe_continue_delegation_from_saved_message(
        thread["thread_id"],
        "alice",
        saved_message,
    )

    delegation_messages = [
        message
        for message in thread_store.list_messages(thread["thread_id"])
        if message.get("msg_type") == "delegation"
    ]
    assert len(delegation_messages) == 1

    review_contributions = thread_store.list_contributions(thread["thread_id"], phase="review")
    assert len(review_contributions) == 1


def test_confirmation_final_answer_does_not_supersede_existing_valid_contribution(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, protocol_store, delegation_store, phase_cycle_store = _build_runner(data_dir)

    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-plan",
                "phases": [
                    {
                        "name": "plan",
                        "actions": ["contribute"],
                        "template": [
                            {"section": "plan", "required": True},
                            {"section": "acceptance_criteria", "required": True},
                        ],
                        "procedure": [{"role": "moderator", "action": "create_plan", "alias": "Plan"}],
                    },
                    {
                        "name": "implement",
                        "actions": ["discuss", "contribute"],
                        "template": [{"section": "implementation", "required": True}],
                        "procedure": [{"role": "developer", "action": "implement", "alias": "Implement"}],
                    },
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Delegate plan thread",
        protocol="delegate-plan",
        participants=["moderator", "developer"],
        initial_phase="plan",
        phase_participants=["moderator"],
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="plan",
            procedure_steps=["create_plan"],
        )
    )
    delegation_store.write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="running",
            loop_iteration=1,
            current_phase="plan",
            current_cycle_index=1,
            target_role="moderator",
            resolved_agent="moderator",
            required_action="submit_contribution",
            expected_output="create_plan contribution",
        )
    )
    thread_store.submit_contribution(
        thread["thread_id"],
        "moderator",
        "plan",
        content="## plan\nBuild it.\n\n## acceptance_criteria\nIt works.\n",
        tldr="valid plan",
    )
    saved_message = runner._save_agent_message(
        thread["thread_id"],
        "moderator",
        "Submitted the `plan` contribution and verified it was recorded.",
        1,
        message_phase="final_answer",
        state_affecting=True,
    )

    runner._maybe_continue_delegation_from_saved_message(
        thread["thread_id"],
        "moderator",
        saved_message,
    )

    plan_contributions = thread_store.list_contributions(thread["thread_id"], phase="plan")
    assert len(plan_contributions) == 1
    assert plan_contributions[0]["_content"] == "## plan\nBuild it.\n\n## acceptance_criteria\nIt works."

    updated_thread = thread_store.get_thread(thread["thread_id"])
    assert updated_thread is not None
    assert updated_thread["current_phase"] == "implement"

    state = delegation_store.read(thread["thread_id"])
    assert state is not None
    assert state.status == "running"
    assert state.current_phase == "implement"
    assert state.resolved_agent == "developer"


def test_helper_result_blocks_when_runtime_recovery_has_failed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, protocol_store, delegation_store, phase_cycle_store = _build_runner(data_dir)

    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-followup",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Review"}],
                    },
                    {
                        "name": "followup",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Follow Up"}],
                    },
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Failed recovery thread",
        protocol="delegate-followup",
        participants=["alice"],
        initial_phase="review",
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    _seed_running_delegation(delegation_store, thread_id=thread["thread_id"])
    session = runner._session_supervisor.ensure_session_nowait(
        thread["thread_id"],
        "alice",
        provider="codex",
    )
    session.degraded = False
    session.recoverable = False
    session.recovery_pending = False
    session.status = "failed"
    session.transport_mode = "live_process_transport"

    runner._persist_invocation_outputs(
        thread["thread_id"],
        "alice",
        InvocationResult(
            ok=True,
            outputs=(
                RuntimeOutput(
                    content="## completed\nReview is complete.\n",
                    phase="final_answer",
                    state_affecting=True,
                ),
            ),
        ),
        chain_depth=1,
    )

    state = delegation_store.read(thread["thread_id"])
    assert state is not None
    assert state.status == "blocked"
    assert state.reason_blocked == "failed_recovery"
    assert state.stop_reason == "failed_recovery"

    delegation_messages = [
        message
        for message in thread_store.list_messages(thread["thread_id"])
        if message.get("msg_type") == "delegation"
    ]
    assert delegation_messages == []


def test_helper_result_fails_when_auto_iteration_cap_is_exceeded(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, protocol_store, delegation_store, phase_cycle_store = _build_runner(data_dir)

    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-followup",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Review"}],
                    },
                    {
                        "name": "followup",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Follow Up"}],
                    },
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Iteration cap thread",
        protocol="delegate-followup",
        participants=["alice"],
        initial_phase="review",
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    delegation_store.write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="running",
            loop_iteration=5,
            current_phase="review",
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent="alice",
            required_action="submit_contribution",
            expected_output="review contribution",
        )
    )

    runner._persist_invocation_outputs(
        thread["thread_id"],
        "alice",
        InvocationResult(
            ok=True,
            outputs=(
                RuntimeOutput(
                    content="## completed\nReview is complete.\n",
                    phase="final_answer",
                    state_affecting=True,
                ),
            ),
        ),
        chain_depth=1,
    )

    state = delegation_store.read(thread["thread_id"])
    assert state is not None
    assert state.status == "failed"
    assert state.stop_reason == "max_auto_iterations_reached"

    delegation_messages = [
        message
        for message in thread_store.list_messages(thread["thread_id"])
        if message.get("msg_type") == "delegation"
    ]
    assert delegation_messages == []


@pytest.mark.asyncio
async def test_direct_delegation_recovers_degraded_session_before_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, _protocol_store, _delegation_store, _phase_cycle_store = _build_runner(data_dir)
    thread = thread_store.create_thread(
        topic="Recoverable direct delegation",
        protocol="delegate-review",
        participants=["alice", "btwin"],
        initial_phase="review",
    )
    session = runner._session_supervisor.ensure_session_nowait(
        thread["thread_id"],
        "alice",
        provider="codex",
    )
    session.primary_transport_mode = "live_process_transport"
    session.transport_mode = "resume_invocation_transport"
    session.fallback_mode = "resume_invocation_transport"
    session.status = "received"
    session.degraded = True
    session.recoverable = True
    session.recovery_pending = False
    session.last_transport_error = "live transport timed out after 180.00s"

    recover_calls: list[tuple[str, str]] = []

    async def fake_recover_for_thread(thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        del bypass_permissions, workspace_root
        recover_calls.append((thread_id, agent_name))
        session.recovery_pending = True
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "recovery_started": True,
        }

    async def fail_if_invoked(thread_id, agent_name, prompt):
        del thread_id, agent_name, prompt
        raise AssertionError("recoverable degraded sessions should recover before direct invoke")

    monkeypatch.setattr(runner, "recover_for_thread", fake_recover_for_thread)
    monkeypatch.setattr(runner, "invoke", fail_if_invoked)

    await runner._handle_message(
        SSEEvent(
            type="message_sent",
            resource_id=thread["thread_id"],
            metadata={
                "from_agent": "btwin",
                "content": "Continue the implementation phase.",
                "chain_depth": 0,
                "delivery_mode": "direct",
                "target_agents": ["alice"],
            },
        )
    )

    assert recover_calls == [(thread["thread_id"], "alice")]
    queued = runner._inbox[(thread["thread_id"], "alice")]
    assert queued.qsize() == 1


@pytest.mark.asyncio
async def test_resume_running_delegation_reattaches_agent_and_replays_pending_inbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, _protocol_store, delegation_store, _phase_cycle_store = _build_runner(data_dir)
    thread = thread_store.create_thread(
        topic="Restarted delegation",
        protocol="delegate-review",
        participants=["alice", "btwin"],
        initial_phase="review",
    )
    thread_store.send_message(
        thread_id=thread["thread_id"],
        from_agent="btwin",
        content="## Delegation Continue\n\nPhase: review\n",
        tldr="delegate review -> alice",
        msg_type="delegation",
        delivery_mode="direct",
        target_agents=["alice"],
    )
    delegation_store.write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="running",
            loop_iteration=1,
            current_phase="review",
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent="alice",
            required_action="submit_contribution",
            expected_output="review contribution",
        )
    )

    attach_calls: list[tuple[str, str]] = []
    drain_calls: list[tuple[str, str, int]] = []

    async def fake_attach_or_resume(thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        del bypass_permissions, workspace_root
        attach_calls.append((thread_id, agent_name))
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "recovery_started": False,
            "reused_session": True,
            "resumed_from_state": False,
        }

    async def fake_drain_inbox(thread_id, agent_name, chain_depth):
        drain_calls.append((thread_id, agent_name, chain_depth))

    monkeypatch.setattr(runner, "attach_or_resume_for_thread", fake_attach_or_resume)
    monkeypatch.setattr(runner, "_drain_inbox", fake_drain_inbox)

    payload = await runner.resume_running_delegation(thread["thread_id"])

    assert payload is not None
    assert payload["status"] == "running"
    assert payload["resolved_agent"] == "alice"
    assert payload["pending_replayed"] == 1
    assert attach_calls == [(thread["thread_id"], "alice")]
    assert runner._inbox[(thread["thread_id"], "alice")].qsize() == 1
    assert drain_calls == [(thread["thread_id"], "alice", 1)]


@pytest.mark.asyncio
async def test_resume_running_delegation_acks_replayed_inbox_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, _protocol_store, delegation_store, _phase_cycle_store = _build_runner(data_dir)
    thread = thread_store.create_thread(
        topic="Restarted delegation ack",
        protocol="delegate-review",
        participants=["alice", "btwin"],
        initial_phase="review",
    )
    thread_store.send_message(
        thread_id=thread["thread_id"],
        from_agent="btwin",
        content="## Delegation Continue\n\nPhase: review\n",
        tldr="delegate review -> alice",
        msg_type="delegation",
        delivery_mode="direct",
        target_agents=["alice"],
    )
    delegation_store.write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="running",
            loop_iteration=1,
            current_phase="review",
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent="alice",
            required_action="submit_contribution",
            expected_output="review contribution",
        )
    )

    async def fake_attach_or_resume(thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        del bypass_permissions, workspace_root
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "recovery_started": False,
            "reused_session": True,
            "resumed_from_state": False,
        }

    delivered_prompts: list[str] = []

    async def fake_invoke(thread_id, agent_name, prompt):
        del thread_id, agent_name
        delivered_prompts.append(prompt)
        return InvocationResult(ok=True)

    monkeypatch.setattr(runner, "attach_or_resume_for_thread", fake_attach_or_resume)
    monkeypatch.setattr(runner, "invoke", fake_invoke)

    first_payload = await runner.resume_running_delegation(thread["thread_id"])

    assert first_payload is not None
    assert first_payload["pending_replayed"] == 1
    assert len(delivered_prompts) == 1
    assert thread_store.list_inbox(thread["thread_id"], "alice") == []

    second_payload = await runner.resume_running_delegation(thread["thread_id"])

    assert second_payload is not None
    assert second_payload["pending_replayed"] == 0
    assert len(delivered_prompts) == 1


@pytest.mark.asyncio
async def test_resume_running_delegation_queues_pending_inbox_before_bootstrap_attach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, _protocol_store, delegation_store, _phase_cycle_store = _build_runner(data_dir)
    thread = thread_store.create_thread(
        topic="Bootstrap replay ordering",
        protocol="delegate-review",
        participants=["alice", "btwin"],
        initial_phase="review",
    )
    thread_store.send_message(
        thread_id=thread["thread_id"],
        from_agent="btwin",
        content="## Delegation Continue\n\nPhase: review\n",
        tldr="delegate review -> alice",
        msg_type="delegation",
        delivery_mode="direct",
        target_agents=["alice"],
    )
    delegation_store.write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="running",
            loop_iteration=1,
            current_phase="review",
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent="alice",
            required_action="submit_contribution",
            expected_output="review contribution",
        )
    )

    queued_during_attach: list[int] = []

    async def fake_attach_or_resume(thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        del bypass_permissions, workspace_root
        queue = runner._inbox.get((thread_id, agent_name))
        queued_during_attach.append(queue.qsize() if queue is not None else 0)
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "recovery_started": False,
            "reused_session": False,
            "resumed_from_state": True,
        }

    monkeypatch.setattr(runner, "attach_or_resume_for_thread", fake_attach_or_resume)

    payload = await runner.resume_running_delegation(thread["thread_id"])

    assert payload is not None
    assert payload["runtime_ensured"] is True
    assert payload["pending_replayed"] == 1
    assert queued_during_attach == [1]
    assert runner._inbox[(thread["thread_id"], "alice")].qsize() == 1


@pytest.mark.asyncio
async def test_resume_running_delegation_discards_replay_queue_when_attach_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, _protocol_store, delegation_store, _phase_cycle_store = _build_runner(data_dir)
    thread = thread_store.create_thread(
        topic="Failed bootstrap replay",
        protocol="delegate-review",
        participants=["alice", "btwin"],
        initial_phase="review",
    )
    thread_store.send_message(
        thread_id=thread["thread_id"],
        from_agent="btwin",
        content="## Delegation Continue\n\nPhase: review\n",
        tldr="delegate review -> alice",
        msg_type="delegation",
        delivery_mode="direct",
        target_agents=["alice"],
    )
    delegation_store.write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="running",
            loop_iteration=1,
            current_phase="review",
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent="alice",
            required_action="submit_contribution",
            expected_output="review contribution",
        )
    )

    async def fake_attach_or_resume(thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        del thread_id, agent_name, bypass_permissions, workspace_root
        return None

    monkeypatch.setattr(runner, "attach_or_resume_for_thread", fake_attach_or_resume)

    payload = await runner.resume_running_delegation(thread["thread_id"])

    assert payload is not None
    assert payload["runtime_ensured"] is False
    assert payload["reason"] == "runtime_attach_failed"
    assert (thread["thread_id"], "alice") not in runner._inbox
    assert len(thread_store.list_inbox(thread["thread_id"], "alice")) == 1


@pytest.mark.asyncio
async def test_resume_running_delegation_blocks_when_replayed_runtime_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, _protocol_store, delegation_store, _phase_cycle_store = _build_runner(data_dir)
    thread = thread_store.create_thread(
        topic="Runtime failure replay",
        protocol="delegate-review",
        participants=["alice", "btwin"],
        initial_phase="review",
    )
    thread_store.send_message(
        thread_id=thread["thread_id"],
        from_agent="btwin",
        content="## Delegation Continue\n\nPhase: review\n",
        tldr="delegate review -> alice",
        msg_type="delegation",
        delivery_mode="direct",
        target_agents=["alice"],
    )
    delegation_store.write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="running",
            loop_iteration=1,
            current_phase="review",
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent="alice",
            required_action="submit_contribution",
            expected_output="review contribution",
        )
    )

    async def fake_attach_or_resume(thread_id, agent_name, *, bypass_permissions=None, workspace_root=None):
        del bypass_permissions, workspace_root
        return {
            "thread_id": thread_id,
            "agent_name": agent_name,
            "status": "thinking",
            "recovery_started": False,
            "reused_session": True,
            "resumed_from_state": False,
        }

    async def fake_drain_inbox(thread_id, agent_name, chain_depth):
        del thread_id, agent_name, chain_depth

    statuses = [
        {
            "thread_id": thread["thread_id"],
            "agent_name": "alice",
            "status": "failed",
            "last_transport_error": "Helper overlay preflight requires a workspace inside a git repo.",
            "recoverable": False,
        }
    ]

    monkeypatch.setattr(runner, "attach_or_resume_for_thread", fake_attach_or_resume)
    monkeypatch.setattr(runner, "_drain_inbox", fake_drain_inbox)
    monkeypatch.setattr(runner, "get_runtime_session_status", lambda _thread_id, _agent_name: statuses[-1])

    payload = await runner.resume_running_delegation(thread["thread_id"])

    assert payload is not None
    assert payload["status"] == "blocked"
    assert payload["reason_blocked"] == "runtime_session_failed"
    assert payload["runtime_status"] == "failed"
    assert "Helper overlay preflight" in payload["runtime_error"]
    assert "btwin live recover --thread" in payload["suggested_next_command"]
    stored = delegation_store.read(thread["thread_id"])
    assert stored is not None
    assert stored.status == "blocked"
    assert stored.reason_blocked == "runtime_session_failed"
