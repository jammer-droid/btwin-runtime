from btwin_core.context_formatter import ContextFormatter


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
