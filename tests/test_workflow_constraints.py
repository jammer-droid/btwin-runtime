from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolSection
from btwin_core.workflow_constraints import evaluate_workflow_hook


def _protocol() -> Protocol:
    return Protocol(
        name="workflow-check",
        description="Protocol for workflow constraint tests",
        phases=[
            ProtocolPhase(
                name="implementation",
                actions=["contribute"],
                template=[
                    ProtocolSection(section="completed", required=True),
                ],
            )
        ],
    )


def test_stop_blocks_when_current_actor_has_no_required_contribution():
    thread = {
        "thread_id": "thread-123",
        "current_phase": "implementation",
        "phase_participants": ["alice"],
    }

    result = evaluate_workflow_hook(
        event="Stop",
        thread=thread,
        protocol=_protocol(),
        actor="alice",
        contributions=[],
    )

    assert result.event == "Stop"
    assert result.decision == "block"
    assert result.reason == "missing_contribution"
    assert result.required_result_recorded is False
    assert "alice" in (result.overlay or "")


def test_stop_allows_when_current_actor_has_required_phase_contribution():
    thread = {
        "thread_id": "thread-123",
        "current_phase": "implementation",
        "phase_participants": ["alice"],
    }
    contributions = [
        {
            "agent": "alice",
            "phase": "implementation",
            "_content": "## completed\nImplemented the requested change.\n",
        }
    ]

    result = evaluate_workflow_hook(
        event="Stop",
        thread=thread,
        protocol=_protocol(),
        actor="alice",
        contributions=contributions,
    )

    assert result.event == "Stop"
    assert result.decision == "allow"
    assert result.reason is None
    assert result.required_result_recorded is True
