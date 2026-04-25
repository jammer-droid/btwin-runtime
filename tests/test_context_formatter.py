from btwin_core.context_formatter import ContextFormatter


def test_context_pack_prompt_uses_current_phase_contract_without_full_protocol_dump() -> None:
    thread = {
        "thread_id": "thread-123",
        "topic": "Large protocol work",
        "protocol": "report-flow",
        "current_phase": "implement",
        "participants": [{"name": "developer"}, {"name": "reviewer"}],
    }
    protocol = {
        "name": "report-flow",
        "description": "Build report",
        "phases": [
            {
                "name": "plan",
                "description": "Plan work",
                "actions": ["contribute"],
                "template": [{"section": "plan", "required": True, "guidance": "Plan only"}],
            },
            {
                "name": "implement",
                "description": "Implement work",
                "actions": ["contribute"],
                "template": [{"section": "implementation", "required": True, "guidance": "Changes"}],
                "procedure": [{"role": "developer", "action": "implement", "alias": "Implement"}],
            },
            {
                "name": "review",
                "description": "Review work",
                "actions": ["review"],
                "template": [{"section": "findings", "required": True, "guidance": "Findings"}],
            },
        ],
    }
    snapshot = ContextFormatter.build_context_pack(
        thread=thread,
        protocol=protocol,
        messages=[
            {"from": "user", "_content": "Please make the report readable."},
            {"from": "reviewer", "_content": "Prior feedback should stay concise."},
        ],
        contributions=[
            {
                "agent": "moderator",
                "phase": "plan",
                "tldr": "Plan readable report",
                "_content": "## plan\n" + ("large plan body\n" * 50),
            },
            {
                "agent": "reviewer",
                "phase": "review",
                "tldr": "Review requested changes",
                "_content": "## findings\nNeeds less raw data.\n\n## verdict\nrequest_changes",
            },
        ],
        agent_name="developer",
    )

    rendered = ContextFormatter.render_context_pack_prompt(snapshot, ask="Continue the implementation.")

    assert "## Context Pack" in rendered
    assert "Current phase: implement" in rendered
    assert "## Current Phase Contract" in rendered
    assert "Implement work" in rendered
    assert "implementation (required): Changes" in rendered
    assert "Plan work" not in rendered
    assert "Review work" not in rendered
    assert "large plan body" not in rendered
    assert "Plan readable report" in rendered
    assert "Review requested changes" in rendered
    assert "Continue the implementation." in rendered


def test_format_initial_context_prefers_cli_contribution_command_for_spawned_helpers() -> None:
    thread = {
        "thread_id": "thread-123",
        "topic": "Prompt test",
        "protocol": "debate",
        "current_phase": "context",
        "participants": [{"name": "alice"}],
    }
    protocol = {
        "name": "debate",
        "description": "Structured discussion",
        "phases": [],
    }

    rendered = ContextFormatter.format_initial_context(
        thread=thread,
        protocol=protocol,
        messages=[],
        contributions=[],
        agent_name="alice",
    )

    assert "btwin contribution submit --thread thread-123 --agent alice --phase context" in rendered
