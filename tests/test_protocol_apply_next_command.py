import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.protocol_store import Protocol, ProtocolInteraction, ProtocolPhase, ProtocolSection, ProtocolStore, ProtocolTransition
from btwin_core.runtime_binding_store import RuntimeBindingStore
from btwin_core.thread_store import ThreadStore


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def _parse_json_output(output: str):
    return json.loads(output.strip())


def _seed_agentless_thread(
    project_root: Path,
    protocol_name: str,
    *,
    participants: list[str] | None = None,
    initial_phase: str = "context",
):
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    thread = thread_store.create_thread(
        topic=f"{protocol_name} thread",
        protocol=protocol_name,
        participants=participants or [],
        initial_phase=initial_phase,
    )
    return thread_store, thread


def _save_protocol(project_root: Path, protocol: Protocol) -> ProtocolStore:
    store = ProtocolStore(project_root / ".btwin" / "protocols")
    store.save_protocol(protocol)
    return store


def _context_protocol() -> Protocol:
    return Protocol(
        name="context-next",
        description="Context phase with manual outcome recording",
        phases=[
            ProtocolPhase(
                name="context",
                actions=["contribute"],
                template=[
                    ProtocolSection(section="background", required=True),
                    ProtocolSection(section="position", required=True),
                ],
            ),
            ProtocolPhase(name="decision", actions=["decide"], decided_by="user"),
        ],
        outcomes=["yes", "no"],
    )


def _close_protocol() -> Protocol:
    return Protocol(
        name="close-next",
        description="Single phase that closes after the required contribution",
        phases=[
            ProtocolPhase(
                name="summary",
                actions=["contribute"],
                template=[
                    ProtocolSection(section="completed", required=True),
                    ProtocolSection(section="remaining", required=True),
                ],
            ),
        ],
    )


def _transition_protocol() -> Protocol:
    return Protocol(
        name="transition-next",
        description="Advance through a branch transition",
        phases=[
            ProtocolPhase(
                name="context",
                actions=["contribute"],
                template=[ProtocolSection(section="background", required=True)],
            ),
            ProtocolPhase(name="followup", actions=["discuss"]),
        ],
        transitions=[ProtocolTransition.model_validate({"from": "context", "to": "followup", "on": "yes"})],
        outcomes=["yes", "no"],
    )


def _review_retry_protocol() -> Protocol:
    return Protocol(
        name="review-retry",
        description="Repeat the same phase until accepted",
        phases=[
            ProtocolPhase(
                name="review",
                description="Review and revise the work.",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[
                    {
                        "role": "reviewer",
                        "action": "review",
                        "guidance": "Review the current implementation state.",
                    },
                    {
                        "role": "implementer",
                        "action": "revise",
                        "guidance": "Implement revisions from review feedback.",
                    },
                ],
            ),
            ProtocolPhase(name="decision", description="Record final acceptance.", actions=["decide"]),
        ],
        transitions=[
            ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry"}),
            ProtocolTransition.model_validate({"from": "review", "to": "decision", "on": "accept"}),
        ],
        outcomes=["retry", "accept"],
    )


def test_protocol_apply_next_preserves_interaction_metadata(tmp_path):
    project_root = tmp_path / "project"
    protocol = _transition_protocol()
    protocol.interaction = ProtocolInteraction(
        mode="orchestrated_chat",
        allow_user_chat=True,
        default_actor="user",
    )

    store = _save_protocol(project_root, protocol)
    loaded = store.get_protocol("transition-next")

    assert loaded is not None
    assert loaded.interaction.mode == "orchestrated_chat"
    assert loaded.interaction.allow_user_chat is True
    assert loaded.interaction.default_actor == "user"


def test_protocol_next_reports_manual_outcome_needed(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store, thread = _seed_agentless_thread(project_root, "context-next", participants=["alice"])
    _save_protocol(project_root, _context_protocol())
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "context",
        content="## background\nKnown.\n\n## position\nSupport the plan.\n",
        tldr="ready for decision",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "next",
            "--thread",
            thread["thread_id"],
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["thread_id"] == thread["thread_id"]
    assert payload["suggested_action"] == "record_outcome"
    assert payload["valid_outcomes"] == ["yes", "no"]
    assert payload["next_phase"] is None


def test_protocol_next_reports_unsupported_outcome_error(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store, thread = _seed_agentless_thread(project_root, "context-next", participants=["alice"])
    _save_protocol(project_root, _context_protocol())
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "context",
        content="## background\nKnown.\n\n## position\nSupport the plan.\n",
        tldr="ready for decision",
    )

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "next",
            "--thread",
            thread["thread_id"],
            "--outcome",
            "maybe",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = _parse_json_output(result.output)
    assert payload["error"] == "unsupported_outcome"
    assert payload["requested_outcome"] == "maybe"
    assert payload["suggested_action"] == "record_outcome"


def test_protocol_apply_next_uses_runtime_binding_and_advances_phase(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store, thread = _seed_agentless_thread(project_root, "transition-next", participants=["alice"])
    _save_protocol(project_root, _transition_protocol())
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "context",
        content="## background\nContext ready.\n",
        tldr="ready to move on",
    )

    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--outcome",
            "yes",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["applied"] is True
    assert payload["thread_source"] == "runtime_binding"
    assert payload["suggested_action"] == "advance_phase"
    assert payload["next_phase"] == "followup"
    updated_thread = thread_store.get_thread(thread["thread_id"])
    assert updated_thread["current_phase"] == "followup"


def test_protocol_apply_next_updates_phase_cycle_state_on_retry(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store, thread = _seed_agentless_thread(project_root, "review-retry", participants=["alice"], initial_phase="review")
    _save_protocol(project_root, _review_retry_protocol())
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "review",
        content="## completed\nNeeds another pass.\n",
        tldr="review retry",
    )

    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--outcome",
            "retry",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["applied"] is True
    assert payload["cycle"]["cycle_index"] == 2
    assert payload["cycle"]["phase_name"] == "review"
    assert payload["context_core"]["current_cycle_index"] == 2
    assert payload["context_core"]["next_expected_role"] == "reviewer"
    assert payload["context_core"]["next_expected_action"] == "Review the current implementation state."
    assert payload["context_core"]["current_step_alias"] == "review"
    assert payload["context_core"]["current_step_role"] == "reviewer"


def test_protocol_apply_next_reports_unsupported_outcome_error(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store, thread = _seed_agentless_thread(project_root, "context-next", participants=["alice"])
    _save_protocol(project_root, _context_protocol())
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "context",
        content="## background\nKnown.\n\n## position\nSupport the plan.\n",
        tldr="ready for decision",
    )

    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--outcome",
            "maybe",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = _parse_json_output(result.output)
    assert payload["error"] == "unsupported_outcome"
    assert payload["requested_outcome"] == "maybe"
    assert payload["applied"] is False


def test_protocol_apply_next_reports_manual_outcome_required(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store, thread = _seed_agentless_thread(project_root, "context-next", participants=["alice"])
    _save_protocol(project_root, _context_protocol())
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "context",
        content="## background\nKnown.\n\n## position\nSupport the plan.\n",
        tldr="ready for decision",
    )

    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["applied"] is False
    assert payload["manual_outcome_required"] is True
    assert payload["suggested_action"] == "record_outcome"


def test_protocol_apply_next_reports_human_hint_when_contribution_is_missing(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    _thread_store, thread = _seed_agentless_thread(project_root, "context-next", participants=["alice"])
    _save_protocol(project_root, _context_protocol())

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--thread",
            thread["thread_id"],
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["applied"] is False
    assert payload["suggested_action"] == "submit_contribution"
    assert "btwin contribution submit" in payload["hint"]


def test_protocol_apply_next_reports_summary_required_when_close_needs_summary(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store, thread = _seed_agentless_thread(
        project_root,
        "close-next",
        participants=["alice"],
        initial_phase="summary",
    )
    _save_protocol(project_root, _close_protocol())
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "summary",
        content="## completed\nDid the work.\n\n## remaining\nNothing.\n",
        tldr="work completed",
    )

    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["applied"] is False
    assert payload["suggested_action"] == "close_thread"
    assert payload["summary_required"] is True


def test_protocol_apply_next_closes_thread_when_summary_provided(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store, thread = _seed_agentless_thread(
        project_root,
        "close-next",
        participants=["alice"],
        initial_phase="summary",
    )
    _save_protocol(project_root, _close_protocol())
    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "summary",
        content="## completed\nDid the work.\n\n## remaining\nNothing.\n",
        tldr="work completed",
    )

    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--summary",
            "All done",
            "--decision",
            "close it",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["applied"] is True
    assert payload["suggested_action"] == "close_thread"
    assert payload["thread"]["status"] == "completed"
    assert payload["thread"]["result_record_id"]
    assert thread_store.get_thread(thread["thread_id"])["status"] == "completed"
    entry_file = next((project_root / ".btwin" / "entries" / "entry").rglob(f"{payload['thread']['result_record_id']}.md"), None)
    assert entry_file is not None, "expected apply-next close to create a btwin entry"
    raw = entry_file.read_text(encoding="utf-8")
    parts = raw.split("---\n", 2)
    assert len(parts) >= 3
    assert "thread-result" in parts[1]
    assert f"thread:{thread['thread_id']}" in parts[1]


def test_protocol_apply_next_attached_advances_via_api(monkeypatch):
    project_root = Path("/tmp/project-attached")
    data_dir = Path("/tmp/data-attached")
    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))

    calls: list[tuple[str, dict | None]] = []

    def fake_get(path: str, params: dict | None = None):
        calls.append((path, params))
        if path == "/api/threads/thread-1":
            return {"thread_id": "thread-1", "protocol": "transition-next", "current_phase": "context"}
        if path == "/api/protocols/transition-next":
            return _transition_protocol().model_dump(by_alias=True)
        if path == "/api/threads/thread-1/contributions":
            assert params == {"phase": "context"}
            return [{"agent": "alice", "_content": "## background\nContext ready.\n"}]
        raise AssertionError(path)

    def fake_post(path: str, data: dict):
        calls.append((path, data))
        assert path == "/api/threads/thread-1/advance-phase"
        assert data == {"nextPhase": "followup"}
        return {"thread_id": "thread-1", "status": "active", "current_phase": "followup"}

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_get)
    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_post)

    result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--thread",
            "thread-1",
            "--outcome",
            "yes",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["applied"] is True
    assert payload["next_phase"] == "followup"
    assert payload["thread"]["current_phase"] == "followup"
    assert calls[0][0] == "/api/threads/thread-1"
    assert calls[1][0] == "/api/protocols/transition-next"
    assert calls[2][0] == "/api/threads/thread-1/contributions"
    assert calls[3][0] == "/api/threads/thread-1/advance-phase"
