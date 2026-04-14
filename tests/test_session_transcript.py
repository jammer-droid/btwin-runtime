from __future__ import annotations

from btwin_core.providers import StreamEvent
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
        StreamEvent(
            event_type="item.completed",
            text_delta="Hello world",
            is_final=True,
            final_text="Hello world",
            raw={
                "type": "item.completed",
                "metadata": {"status": "ok"},
                "item": {"type": "agent_message", "text": "Hello world"},
            },
        ),
        {
            "kind": "turn/completed",
            "turn": {"id": "turn-9"},
            "metadata": {"status": "ok"},
        },
        {"kind": "tool_use", "content": "search"},
    ]

    normalized = normalize_runtime_events(events, provider_name="codex")

    assert [(event.kind, event.content) for event in normalized] == [
        ("session_started", "thread-123"),
        ("text_delta", "Hello "),
        ("turn_complete", "Hello world"),
        ("turn_complete", None),
    ]
    assert normalized[0].metadata["provider"] == "codex"
    assert normalized[0].metadata["transport"] == "app-server"
    assert normalized[2].metadata["provider"] == "codex"
    assert normalized[2].metadata["status"] == "ok"
    assert normalized[3].metadata["status"] == "ok"
    assert all(event.content != "search" for event in normalized)


def test_normalize_runtime_events_keeps_nested_raw_thread_started_session_id() -> None:
    normalized = normalize_runtime_events(
        [
            {
                "event_type": "thread.started",
                "raw": {"type": "thread.started", "thread_id": "thread-123"},
            }
        ],
        provider_name="codex",
    )

    assert [(event.kind, event.content) for event in normalized] == [
        ("session_started", "thread-123"),
    ]


def test_normalize_runtime_events_does_not_promote_startup_text_to_session_id() -> None:
    normalized = normalize_runtime_events(
        [
            {
                "event_type": "system/init",
                "message": {"content": [{"type": "text", "text": "noise"}]},
            }
        ],
        provider_name="claude-code",
    )

    assert normalized == []


def test_normalize_runtime_events_filters_hook_marked_startup_session_started_event() -> None:
    normalized = normalize_runtime_events(
        [
            {
                "event_type": "thread.started",
                "session_id": "thread-123",
                "metadata": {"source": "hook", "phase": "startup"},
            }
        ],
        provider_name="codex",
    )

    assert normalized == []


def test_normalize_runtime_events_keeps_mapping_item_completed_text() -> None:
    normalized = normalize_runtime_events(
        [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Mapped final"},
                "metadata": {"source": "raw"},
            }
        ],
        provider_name="codex",
    )

    assert [(event.kind, event.content) for event in normalized] == [
        ("turn_complete", "Mapped final"),
    ]
    assert normalized[0].metadata["source"] == "raw"


def test_normalize_runtime_events_keeps_turn_completed_marker_without_text() -> None:
    normalized = normalize_runtime_events(
        [
            {
                "kind": "turn/completed",
                "turn": {"id": "turn-9"},
                "metadata": {"status": "ok"},
            }
        ],
        provider_name="codex",
    )

    assert len(normalized) == 1
    assert normalized[0].kind == "turn_complete"
    assert normalized[0].content is None
    assert normalized[0].metadata["status"] == "ok"
    assert normalized[0].metadata["provider"] == "codex"


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


def test_normalize_runtime_events_filters_startup_hook_and_tool_noise() -> None:
    normalized = normalize_runtime_events(
        [
            {"kind": "system"},
            {"kind": "hook", "name": "pre_tool"},
            {"kind": "tool_use", "content": "search"},
            {"kind": "item/agentMessage/delta", "delta": "Hello "},
            {"event_type": "assistant", "message": {"content": [{"type": "text", "text": "world"}]}},
        ],
        provider_name="codex",
    )

    assert [(event.kind, event.content) for event in normalized] == [
        ("text_delta", "Hello "),
        ("text_delta", "world"),
    ]


def test_normalize_runtime_events_filters_hook_and_status_noise_even_when_wrapped_like_transcript_events() -> None:
    normalized = normalize_runtime_events(
        [
            {
                "event_type": "assistant",
                "message": {"content": [{"type": "text", "text": "booting"}]},
                "metadata": {"source": "hook", "phase": "startup"},
            },
            {
                "event_type": "result",
                "result": "ready",
                "metadata": {"source": "status_notification"},
            },
            {
                "event_type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello"}]},
            },
            {
                "event_type": "result",
                "result": "done",
            },
        ],
        provider_name="claude-code",
    )

    assert [(event.kind, event.content) for event in normalized] == [
        ("text_delta", "Hello"),
        ("turn_complete", "done"),
    ]
