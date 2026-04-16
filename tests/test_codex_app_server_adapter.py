from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from btwin_core.prototypes.persistent_sessions.codex_app_server_adapter import (
    CodexAppServerPersistentAdapter,
)
from btwin_core.prototypes.persistent_sessions.types import SessionConfig
from btwin_core.session_transports import TransportLaunchContext


class _FakeStream:
    def __aiter__(self):
        async def iterator():
            if False:
                yield b""

        return iterator()


class _FakeStdin:
    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.pid = 1234
        self.returncode = None

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        self.returncode = 0
        return 0


def test_codex_app_server_launch_command_enables_hooks() -> None:
    adapter = CodexAppServerPersistentAdapter()

    command = adapter._build_launch_command(SessionConfig())

    assert command[:4] == ["codex", "-a", "never", "-s"]
    assert "--enable" in command
    enable_index = command.index("--enable")
    assert command[enable_index + 1] == "codex_hooks"
    assert "app-server" in command


def test_codex_app_server_launch_command_includes_config_overrides() -> None:
    adapter = CodexAppServerPersistentAdapter()

    command = adapter._build_launch_command(
        SessionConfig(
            options={
                "config_overrides": {
                    "developer_instructions": "You are the managed helper.\nStay brief.",
                }
            }
        )
    )

    config_index = command.index("-c")
    assert command[config_index + 1] == 'developer_instructions="You are the managed helper.\\nStay brief."'


def test_live_transport_session_config_carries_cwd() -> None:
    launch_context = TransportLaunchContext(
        provider_name="codex",
        transport_mode="live_process_transport",
        env={"FOO": "bar"},
        cwd="/tmp/project-root",
        config_overrides={"developer_instructions": "Stay brief."},
    )

    config = launch_context.build_session_config()

    assert config.options["env"] == {"FOO": "bar"}
    assert config.options["cwd"] == "/tmp/project-root"
    assert config.options["config_overrides"] == {"developer_instructions": "Stay brief."}


@pytest.mark.asyncio
async def test_codex_app_server_start_uses_configured_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = CodexAppServerPersistentAdapter()
    captured_kwargs: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args
        captured_kwargs.update(kwargs)
        return _FakeProcess()

    async def fake_send_request(method: str, params: dict[str, object]):  # noqa: ANN202
        if method == "initialize":
            return {}
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        raise AssertionError(method)

    async def fake_send_notification(_method: str, _params: dict[str, object]) -> None:
        return None

    async def fake_pump_stdout() -> None:
        return None

    async def fake_pump_stderr() -> None:
        return None

    monkeypatch.setattr(
        "btwin_core.prototypes.persistent_sessions.codex_app_server_adapter.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(adapter, "_send_request", fake_send_request)
    monkeypatch.setattr(adapter, "_send_notification", fake_send_notification)
    monkeypatch.setattr(adapter, "_pump_stdout", fake_pump_stdout)
    monkeypatch.setattr(adapter, "_pump_stderr", fake_pump_stderr)

    cwd = tmp_path / "project-root"
    cwd.mkdir()

    result = await adapter.start(SessionConfig(options={"cwd": str(cwd)}))

    assert result.metadata["ok"] is True
    assert captured_kwargs["cwd"] == str(cwd)


def test_codex_app_server_parse_notification_keeps_agent_message_completion_text_and_phase() -> None:
    adapter = CodexAppServerPersistentAdapter()

    event = adapter._parse_notification(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "type": "agentMessage",
                    "id": "msg-1",
                    "text": "Done",
                    "phase": "final_answer",
                },
            },
        }
    )

    assert event is not None
    assert event.kind == "agent_message_completed"
    assert event.content == "Done"
    assert event.metadata["phase"] == "final_answer"
    assert event.metadata["item_id"] == "msg-1"


def test_codex_app_server_parse_error_notification_keeps_retry_and_error_info() -> None:
    adapter = CodexAppServerPersistentAdapter()

    event = adapter._parse_notification(
        {
            "method": "error",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "willRetry": True,
                "error": {
                    "message": "stream disconnected",
                    "additionalDetails": "connection reset",
                    "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 502}},
                },
            },
        }
    )

    assert event is not None
    assert event.kind == "turn_error"
    assert event.content == "stream disconnected"
    assert event.metadata["turn_id"] == "turn-1"
    assert event.metadata["will_retry"] is True
    assert event.metadata["additional_details"] == "connection reset"
    assert event.metadata["codex_error_info"] == {"responseStreamDisconnected": {"httpStatusCode": 502}}
