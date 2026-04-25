import json
import logging
from pathlib import Path

import httpx
from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.delegation_state import DelegationState
from btwin_core.delegation_store import DelegationStore
from btwin_core.protocol_store import (
    Protocol,
    ProtocolAuthoringGate,
    ProtocolAuthoringGateRoute,
    ProtocolGuardSet,
    ProtocolOutcomePolicy,
    ProtocolPhase,
    ProtocolProcedureStep,
    ProtocolSection,
    ProtocolStore,
    ProtocolTransition,
)
from btwin_core.system_mailbox_store import SystemMailboxStore
from btwin_core.thread_store import ThreadStore
from btwin_core.workflow_event_log import WorkflowEventLog


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def _report_protocol() -> Protocol:
    return Protocol(
        name="report-flow",
        description="Builds a report",
        roles=["moderator", "developer"],
        outcomes=["approve", "request_changes"],
        guard_sets=[
            ProtocolGuardSet(
                name="contribution-gate",
                description="Requires phase output",
                guards=["contribution_required"],
            )
        ],
        gates=[
            ProtocolAuthoringGate(
                name="review-gate",
                routes=[
                    ProtocolAuthoringGateRoute(outcome="approve", target_phase="final"),
                    ProtocolAuthoringGateRoute(outcome="request_changes", target_phase="implement"),
                ],
            )
        ],
        outcome_policies=[
            ProtocolOutcomePolicy(
                name="review-policy",
                emitters=["reviewer"],
                actions=["review"],
                outcomes=["approve", "request_changes"],
            )
        ],
        phases=[
            ProtocolPhase(
                name="plan",
                description="Plan the work",
                actions=["contribute"],
                template=[ProtocolSection(section="plan", required=True)],
                procedure=[ProtocolProcedureStep(role="moderator", action="submit_plan", alias="Plan")],
                guard_set="contribution-gate",
            ),
            ProtocolPhase(
                name="implement",
                description="Implement the work",
                actions=["contribute"],
                template=[ProtocolSection(section="implementation", required=True)],
                procedure=[ProtocolProcedureStep(role="developer", action="submit_implementation", alias="Implement")],
                gate="review-gate",
                outcome_policy="review-policy",
            ),
            ProtocolPhase(name="final", actions=["decide"]),
        ],
        transitions=[
            ProtocolTransition(**{"from": "plan", "to": "implement", "on": "complete", "alias": "Start implementation"}),
            ProtocolTransition(**{"from": "implement", "to": "final", "on": "approve", "alias": "Approve"}),
            ProtocolTransition(**{"from": "implement", "to": "implement", "on": "request_changes", "alias": "Request changes"}),
        ],
    )


def _parse_json_output(output: str) -> dict:
    return json.loads(output.strip())


def test_thread_export_report_standalone_writes_self_contained_html(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    ProtocolStore(project_root / ".btwin" / "protocols").save_protocol(_report_protocol())
    AgentStore(data_dir).register(
        "developer",
        model="gpt-5.5",
        provider="codex",
        role="developer",
        reasoning_level="high",
    )
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    thread = thread_store.create_thread(
        topic="Static <Report> Export!",
        protocol="report-flow",
        participants=["moderator", "developer"],
        initial_phase="implement",
        phase_participants=["developer"],
    )
    thread_store.send_message(
        thread["thread_id"],
        from_agent="moderator",
        content="Please implement <script>alert('x')</script> safely.",
        tldr="implementation request",
        msg_type="proposal",
    )
    thread_store.submit_contribution(
        thread["thread_id"],
        "developer",
        "implement",
        content="## implementation\n\nExporter writes **inline** HTML.",
        tldr="exporter implemented",
    )
    WorkflowEventLog(thread_store.workflow_event_log_path(thread["thread_id"])).append(
        {
            "timestamp": "2026-04-25T01:02:03+00:00",
            "thread_id": thread["thread_id"],
            "event_type": "delegation_dispatched",
            "source": "btwin.delegate",
            "summary": "Dispatched implement work",
        }
    )
    SystemMailboxStore(project_root / ".btwin").append_report(
        {
            "created_at": "2026-04-25T01:03:00+00:00",
            "thread_id": thread["thread_id"],
            "report_type": "cycle_result",
            "summary": "Implementation cycle finished",
            "phase": "implement",
            "cycle_finished": True,
        }
    )
    DelegationStore(project_root / ".btwin").write(
        DelegationState(
            thread_id=thread["thread_id"],
            status="running",
            current_phase="implement",
            target_role="developer",
            resolved_agent="developer",
            required_action="submit_contribution",
            expected_output="implement_report_export contribution",
        )
    )

    result = runner.invoke(app, ["thread", "export-report", "--thread", thread["thread_id"], "--json"])

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    report_path = Path(payload["path"])
    assert report_path == project_root / "docs" / "local" / "reports" / "2026-04-25-static-report-export-report.html"
    assert report_path.exists()
    html = report_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert "Static &lt;Report&gt; Export!" in html
    assert "report-flow" in html
    assert "Plan the work" in html
    assert "Start implementation" in html
    assert "contribution_required" in html
    assert "review-policy" in html
    assert "developer" in html
    assert "gpt-5.5" in html
    assert "high" in html
    assert "running" in html
    assert "implementation request" in html
    assert "Exporter writes **inline** HTML." in html
    assert "delegation_dispatched" in html
    assert "Implementation cycle finished" in html
    assert "<script" not in html.lower()
    assert "src=\"http" not in html.lower()
    assert "href=\"http" not in html.lower()


def test_thread_report_command_uses_planned_positional_cli_shape(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    ProtocolStore(project_root / ".btwin" / "protocols").save_protocol(_report_protocol())
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    thread = thread_store.create_thread(
        topic="Planned Report CLI",
        protocol="report-flow",
        participants=["developer"],
        initial_phase="implement",
        phase_participants=["developer"],
    )
    thread_store.submit_contribution(
        thread["thread_id"],
        "developer",
        "implement",
        content="## implementation\n\nUses the planned command shape.",
        tldr="planned command implemented",
    )

    output_path = tmp_path / "planned-report.html"
    result = runner.invoke(
        app,
        ["thread", "report", thread["thread_id"], "--output", str(output_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["path"] == str(output_path)
    html = output_path.read_text(encoding="utf-8")
    assert "Planned Report CLI" in html
    assert "Uses the planned command shape." in html


def test_thread_report_command_requires_overwrite_for_existing_output(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    ProtocolStore(project_root / ".btwin" / "protocols").save_protocol(_report_protocol())
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    thread = thread_store.create_thread(
        topic="Overwrite Guard",
        protocol="report-flow",
        participants=["developer"],
        initial_phase="implement",
        phase_participants=["developer"],
    )

    output_path = tmp_path / "existing-report.html"
    output_path.write_text("keep me\n", encoding="utf-8")

    blocked = runner.invoke(
        app,
        ["thread", "report", thread["thread_id"], "--output", str(output_path), "--json"],
    )

    assert blocked.exit_code == 2, blocked.output
    assert "already exists" in blocked.output
    assert "--overwrite" in blocked.output
    assert output_path.read_text(encoding="utf-8") == "keep me\n"

    overwritten = runner.invoke(
        app,
        [
            "thread",
            "report",
            thread["thread_id"],
            "--output",
            str(output_path),
            "--overwrite",
            "--json",
        ],
    )

    assert overwritten.exit_code == 0, overwritten.output
    assert "Overwrite Guard" in output_path.read_text(encoding="utf-8")


def test_thread_export_report_attached_uses_existing_shared_routes(tmp_path, monkeypatch):
    data_dir = tmp_path / ".btwin"
    project_root = tmp_path / "project"
    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_service_data_dir", lambda: data_dir)
    WorkflowEventLog(data_dir / "threads" / "thread-1" / "workflow-events.jsonl").append(
        {
            "timestamp": "2026-04-25T01:02:03+00:00",
            "thread_id": "thread-1",
            "event_type": "delegate_dispatch",
            "source": "btwin.delegate",
            "summary": "Attached dispatch event",
        }
    )

    calls: list[tuple[str, dict | None]] = []

    def fake_get(path: str, params: dict | None = None):
        calls.append((path, params))
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached Export",
                "protocol": "report-flow",
                "status": "active",
                "current_phase": "implement",
                "participants": [{"name": "developer", "joined_at": "2026-04-25T00:00:00+00:00"}],
                "phase_participants": ["developer"],
            }
        if path == "/api/threads/thread-1/status":
            return {"thread_id": "thread-1", "current_phase": "implement", "agents": []}
        if path == "/api/protocols/report-flow":
            return _report_protocol().model_dump(by_alias=True)
        if path == "/api/threads/thread-1/messages":
            return [{"message_id": "msg-1", "created_at": "2026-04-25T01:00:00+00:00", "from": "moderator", "tldr": "attached message", "_content": "Hello"}]
        if path == "/api/threads/thread-1/contributions":
            return [{"contribution_id": "contrib-1", "created_at": "2026-04-25T01:01:00+00:00", "agent": "developer", "phase": "implement", "tldr": "attached contribution", "_content": "Done"}]
        if path == "/api/system-mailbox":
            return {"count": 1, "reports": [{"created_at": "2026-04-25T01:03:00+00:00", "thread_id": "thread-1", "report_type": "cycle_result", "summary": "Attached cycle"}]}
        if path == "/api/threads/thread-1/phase-cycle":
            return {"state": {"thread_id": "thread-1", "active_phase": "implement"}}
        if path == "/api/threads/thread-1/delegate/status":
            return {"thread_id": "thread-1", "status": "running", "resolved_agent": "developer"}
        if path == "/api/agents":
            return [{"name": "developer", "model": "gpt-5.5", "provider": "codex", "reasoning_level": "high", "role": "developer"}]
        if path == "/api/agent-runtime-status":
            return {"agents": {"developer": [{"thread_id": "thread-1", "provider": "codex", "status": "active"}]}}
        raise AssertionError(f"unexpected GET path: {path}")

    monkeypatch.setattr(main, "_api_get", fake_get)
    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_get)

    output_path = tmp_path / "attached-report.html"
    result = runner.invoke(
        app,
        ["thread", "export-report", "--thread", "thread-1", "--output", str(output_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert output_path.exists()
    html = output_path.read_text(encoding="utf-8")
    assert "Attached Export" in html
    assert "attached contribution" in html
    assert "Attached dispatch event" in html
    assert "gpt-5.5" in html
    assert ("/api/report", None) not in calls
    assert calls == [
        ("/api/threads/thread-1", None),
        ("/api/threads/thread-1/status", None),
        ("/api/protocols/report-flow", None),
        ("/api/threads/thread-1/messages", None),
        ("/api/threads/thread-1/contributions", None),
        ("/api/system-mailbox", {"threadId": "thread-1", "limit": 200}),
        ("/api/threads/thread-1/phase-cycle", None),
        ("/api/threads/thread-1/delegate/status", None),
        ("/api/agents", None),
        ("/api/agent-runtime-status", None),
    ]


def test_thread_export_report_attached_ignores_missing_optional_sources(tmp_path, monkeypatch, caplog):
    data_dir = tmp_path / ".btwin"
    project_root = tmp_path / "project"
    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_service_data_dir", lambda: data_dir)

    def optional_404(path: str) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", f"http://127.0.0.1:8787{path}")
        response = httpx.Response(404, request=request)
        return httpx.HTTPStatusError("not found", request=request, response=response)

    def fake_get(path: str, params: dict | None = None):
        if path == "/api/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "topic": "Attached Export With Missing Optional Sources",
                "protocol": "report-flow",
                "status": "active",
                "current_phase": "implement",
                "participants": [{"name": "developer"}],
            }
        if path == "/api/threads/thread-1/status":
            return {"thread_id": "thread-1", "current_phase": "implement", "agents": []}
        if path == "/api/protocols/report-flow":
            return _report_protocol().model_dump(by_alias=True)
        if path == "/api/threads/thread-1/messages":
            return []
        if path == "/api/threads/thread-1/contributions":
            return []
        if path == "/api/system-mailbox":
            return {"count": 0, "reports": []}
        if path in {
            "/api/threads/thread-1/phase-cycle",
            "/api/threads/thread-1/delegate/status",
            "/api/agents",
            "/api/agent-runtime-status",
        }:
            raise optional_404(path)
        raise AssertionError(f"unexpected GET path: {path}")

    monkeypatch.setattr(main, "_api_get", fake_get)
    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_get)

    output_path = tmp_path / "attached-report.html"
    with caplog.at_level(logging.WARNING, logger="btwin_cli.main"):
        result = runner.invoke(
            app,
            ["thread", "export-report", "--thread", "thread-1", "--output", str(output_path), "--json"],
        )

    assert result.exit_code == 0, result.output
    html = output_path.read_text(encoding="utf-8")
    assert "Attached Export With Missing Optional Sources" in html
    assert "No delegation state recorded for this thread." in html
    assert not [
        record
        for record in caplog.records
        if "Optional attached report source unavailable" in record.message
    ]
