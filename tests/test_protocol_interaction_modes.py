from pathlib import Path

import pytest

from btwin_core.protocol_store import ProtocolStore
from pydantic import ValidationError

from btwin_core.protocol_store import Protocol, ProtocolGuardSet, ProtocolPhase


def test_protocol_store_parses_interaction_mode(tmp_path: Path):
    path = tmp_path / "protocols"
    path.mkdir()
    (path / "debate.yaml").write_text(
        """
name: debate
phases:
  - name: discussion
    actions: [discuss]
interaction:
  mode: orchestrated_chat
  allow_user_chat: true
  default_actor: user
""",
        encoding="utf-8",
    )

    store = ProtocolStore(path)
    proto = store.get_protocol("debate")

    assert proto is not None
    assert proto.interaction.mode == "orchestrated_chat"
    assert proto.interaction.allow_user_chat is True
    assert proto.interaction.default_actor == "user"


def test_protocol_rejects_unknown_guard_set_reference():
    with pytest.raises(ValidationError, match="guard_set"):
        Protocol.model_validate(
            {
                "name": "debate",
                "guard_sets": [
                    {"name": "discussion-guards", "guards": ["contribution_required"]},
                ],
                "phases": [
                    {
                        "name": "discussion",
                        "actions": [ "discuss" ],
                        "guard_set": "missing-guards",
                    }
                ],
            }
        )


def test_protocol_rejects_unknown_guard_name():
    with pytest.raises(ValidationError, match="unsupported guard"):
        Protocol(
            name="debate",
            guard_sets=[
                ProtocolGuardSet(
                    name="discussion-guards",
                    guards=["contribution_required", "not_a_real_guard"],
                )
            ],
            phases=[
                ProtocolPhase(
                    name="discussion",
                    actions=["discuss"],
                    guard_set="discussion-guards",
                )
            ],
        )
