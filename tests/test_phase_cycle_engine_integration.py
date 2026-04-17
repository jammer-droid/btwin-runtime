import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_store import PhaseCycleStore
from btwin_core.protocol_store import Protocol, ProtocolPhase, ProtocolSection, ProtocolStore, ProtocolTransition
from btwin_core.runtime_binding_store import RuntimeBindingStore
from btwin_core.system_mailbox_store import SystemMailboxStore
from btwin_core.thread_store import ThreadStore
from btwin_core.workflow_event_log import WorkflowEventLog


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _review_retry_protocol() -> Protocol:
    return Protocol(
        name="review-retry",
        description="Repeat review until accepted.",
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
                        "alias": "Review",
                        "guidance": "Review the current implementation state.",
                    },
                    {
                        "role": "implementer",
                        "action": "revise",
                        "alias": "Revise",
                        "guidance": "Implement revisions from review feedback.",
                    },
                ],
            ),
            ProtocolPhase(
                name="decision",
                description="Record the final decision.",
                actions=["decide"],
            ),
        ],
        transitions=[
            ProtocolTransition.model_validate({"from": "review", "to": "review", "on": "retry", "alias": "Retry Gate"}),
            ProtocolTransition.model_validate({"from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate"}),
        ],
        outcomes=["retry", "accept"],
    )


def _codex_hook_payload(event_name: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "session_id": "codex-session-1",
        "transcript_path": None,
        "cwd": "",
        "hook_event_name": event_name,
        "model": "gpt-5.4",
        "turn_id": "turn-1",
    }
    if event_name == "Stop":
        payload["stop_hook_active"] = False
        payload["last_assistant_message"] = "done"
    if event_name == "UserPromptSubmit":
        payload["prompt"] = "continue"
    return payload


def test_phase_cycle_retry_then_accept_flow_records_ordered_mailbox_and_events(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(_review_retry_protocol())
    thread = thread_store.create_thread(
        topic="Review retry integration",
        protocol="review-retry",
        participants=["alice"],
        initial_phase="review",
    )
    PhaseCycleStore(project_root / ".btwin").write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review", "revise"],
        )
    )
    RuntimeBindingStore(project_root / ".btwin").bind(thread["thread_id"], "alice")

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    stop_payload = _codex_hook_payload("Stop")
    stop_payload["cwd"] = str(project_root)
    stop_result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(stop_payload))
    assert stop_result.exit_code == 0, stop_result.output
    stop_output = json.loads(stop_result.output)
    assert stop_output["decision"] == "block"
    assert "needs a contribution" in stop_output["reason"]

    thread_store.submit_contribution(
        thread["thread_id"],
        "alice",
        "review",
        content="## completed\nNeeds another pass.\n",
        tldr="retry requested",
    )

    prompt_payload = _codex_hook_payload("UserPromptSubmit")
    prompt_payload["cwd"] = str(project_root)
    prompt_result = runner.invoke(app, ["workflow", "hook"], input=json.dumps(prompt_payload))
    assert prompt_result.exit_code == 0, prompt_result.output
    assert prompt_result.output.strip() == ""

    retry_result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--thread",
            thread["thread_id"],
            "--outcome",
            "retry",
            "--json",
        ],
    )
    assert retry_result.exit_code == 0, retry_result.output
    retry_payload = json.loads(retry_result.output)
    assert retry_payload["applied"] is True
    assert retry_payload["cycle"]["cycle_index"] == 2
    assert retry_payload["cycle"]["phase_name"] == "review"
    assert retry_payload["cycle"]["current_step_label"] == "review"
    assert retry_payload["context_core"]["current_cycle_index"] == 2
    assert retry_payload["context_core"]["current_step_label"] == "review"

    second_retry_result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--thread",
            thread["thread_id"],
            "--outcome",
            "retry",
            "--json",
        ],
    )
    assert second_retry_result.exit_code == 0, second_retry_result.output
    second_retry_payload = json.loads(second_retry_result.output)
    assert second_retry_payload["applied"] is True
    assert second_retry_payload["cycle"]["cycle_index"] == 3
    assert second_retry_payload["cycle"]["phase_name"] == "review"
    assert second_retry_payload["cycle"]["current_step_label"] == "review"
    assert second_retry_payload["context_core"]["current_cycle_index"] == 3
    assert second_retry_payload["context_core"]["current_step_label"] == "review"

    accept_result = runner.invoke(
        app,
        [
            "protocol",
            "apply-next",
            "--thread",
            thread["thread_id"],
            "--outcome",
            "accept",
            "--json",
        ],
    )
    assert accept_result.exit_code == 0, accept_result.output
    accept_payload = json.loads(accept_result.output)
    assert accept_payload["applied"] is True
    assert accept_payload["cycle"]["cycle_index"] == 1
    assert accept_payload["cycle"]["phase_name"] == "decision"
    assert accept_payload["context_core"]["current_cycle_index"] == 1
    assert accept_payload["thread"]["current_phase"] == "decision"

    reports = SystemMailboxStore(project_root / ".btwin").list_reports(thread_id=thread["thread_id"])
    assert [report["next_cycle_index"] for report in reports] == [1, 3, 2]
    assert reports[0]["summary"] == "Phase `review` complete; advanced to `decision`."
    assert reports[1]["summary"] == "Phase `review` requested retry; continuing in `review` with active cycle 3."
    assert reports[2]["summary"] == "Phase `review` requested retry; continuing in `review` with active cycle 2."

    events = WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).list_events()
    assert [event["event_type"] for event in events] == [
        "phase_exit_check_requested",
        "phase_exit_blocked",
        "phase_attempt_started",
        "cycle_gate_completed",
        "cycle_gate_completed",
        "cycle_gate_completed",
    ]
    assert events[1]["scope"] == "local_recovery"
