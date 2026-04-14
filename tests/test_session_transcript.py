from __future__ import annotations

from btwin_core.session_transcript import normalize_runtime_events


def test_normalize_runtime_events_keeps_transcript_worthy_codex_events() -> None:
    events = [
        {
            "kind": "thread.started",
            "thread": {"id": "thread-123"},
            "metadata": {"transport": "app-server"},
        },
        {"kind": "system"},
        {"kind": "item/agentMessage/delta", "delta": "Hello "},
        {
            "event_type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "world"},
                    {"type": "tool_use", "name": "ignored"},
                ]
            },
        },
        {"kind": "tool_use", "content": "search"},
        {
            "kind": "turn/completed",
            "turn": {"id": "turn-9"},
            "metadata": {"status": "ok"},
        },
    ]

    normalized = normalize_runtime_events(events, provider_name="codex")

    assert [(event.kind, event.content) for event in normalized] == [
        ("session_started", "thread-123"),
        ("text_delta", "Hello "),
        ("text_delta", "world"),
        ("turn_complete", "turn-9"),
    ]
    assert normalized[0].metadata["provider"] == "codex"
    assert normalized[0].metadata["transport"] == "app-server"
    assert normalized[-1].metadata["provider"] == "codex"
    assert normalized[-1].metadata["status"] == "ok"


def test_normalize_runtime_events_keeps_claude_style_result_completion() -> None:
    events = [
        {
            "event_type": "system/init",
            "session_id": "claude-session",
            "metadata": {"source": "claude"},
        },
        {
            "event_type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Alpha "},
                    {"type": "text", "text": "Beta"},
                    {"type": "tool_use", "name": "ignored"},
                ]
            },
        },
        {"event_type": "result", "result": "done", "metadata": {"status": "complete"}},
        {"event_type": "hook", "name": "on_stop"},
    ]

    normalized = normalize_runtime_events(events, provider_name="claude")

    assert [(event.kind, event.content) for event in normalized] == [
        ("session_started", "claude-session"),
        ("text_delta", "Alpha Beta"),
        ("turn_complete", "done"),
    ]
    assert normalized[0].metadata["provider"] == "claude"
    assert normalized[0].metadata["source"] == "claude"
    assert normalized[-1].metadata["status"] == "complete"
