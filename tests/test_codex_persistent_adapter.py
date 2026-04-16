from __future__ import annotations

import pytest

from btwin_core.prototypes.persistent_sessions.codex_adapter import CodexPersistentAdapter
from btwin_core.prototypes.persistent_sessions.types import SessionConfig


@pytest.mark.asyncio
async def test_codex_exec_launch_command_includes_config_overrides() -> None:
    adapter = CodexPersistentAdapter()

    await adapter.start(
        SessionConfig(
            options={
                "config_overrides": {
                    "developer_instructions": "You are the managed helper.\nStay brief.",
                }
            }
        )
    )

    command = adapter._build_launch_command(None)

    config_index = command.index("-c")
    assert command[config_index + 1] == 'developer_instructions="You are the managed helper.\\nStay brief."'
