from __future__ import annotations

from btwin_core.providers import StreamEvent
from btwin_core.session_transcript import normalize_runtime_events


def test_normalize_runtime_events_keeps_codex_stream_event_completion() -> None:
    events = [
        StreamEvent(
            event_type="thread.started",
            session_id="thread-123",
            raw={"type": "thread.started", "thread_id": "thread-123"},
        ),
        StreamEvent(
            event_type="item.completed",
            raw={
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Hello world"},
                "metadata": {"status": "ok"},
            },
        ),
    ]

    normalized = normalize_runtime_events(events, provider_name="codex")

    assert [(event.kind, event.content) for event in normalized] == [
        ("session_started", "thread-123"),
        ("turn_complete", "Hello world"),
    ]
    assert normalized[-1].metadata["status"] == "ok"


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
    normalized = normalize_runtime_events(
        [
            {
                "event_type": "system/init",
                "session_id": "claude-session",
            },
            {
                "event_type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Alpha "},
                        {"type": "text", "text": "Beta"},
                    ]
                },
            },
            {
                "event_type": "result",
                "result": "done",
                "metadata": {"status": "complete"},
            },
        ],
        provider_name="claude-code",
    )

    assert [(event.kind, event.content) for event in normalized] == [
        ("session_started", "claude-session"),
        ("text_delta", "Alpha Beta"),
        ("turn_complete", "done"),
    ]
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
