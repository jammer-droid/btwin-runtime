import json
from pathlib import Path

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from typer.testing import CliRunner

runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _authoring_protocol_yaml() -> str:
    return "\n".join(
        [
            "name: review-loop",
            "description: Authoring-first review loop",
            "phases:",
            "  - name: review",
            "    actions: [contribute]",
            "    gate: review-gate",
            "    outcome_policy: review-outcomes",
            "  - name: decision",
            "    actions: [decide]",
            "    decided_by: user",
            "gates:",
            "  - name: review-gate",
            "    routes:",
            "      - outcome: retry",
            "        target_phase: review",
            "        alias: Retry Loop",
            "        key: retry-loop",
            "      - outcome: accept",
            "        target_phase: decision",
            "        alias: Accept Gate",
            "        key: accept-gate",
            "outcome_policies:",
            "  - name: review-outcomes",
            "    emitters: [reviewer, user]",
            "    actions: [decide]",
            "    outcomes: [retry, accept]",
        ]
    ) + "\n"


def _parse_json_output(output: str):
    return json.loads(output.strip())


def test_protocol_create_saves_compiled_authoring_protocol(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(tmp_path / ".btwin"))
    protocol_path = tmp_path / "review-loop.yaml"
    protocol_path.write_text(_authoring_protocol_yaml(), encoding="utf-8")

    result = runner.invoke(
        app,
        ["protocol", "create", "--file", str(protocol_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["saved"] is True
    assert payload["name"] == "review-loop"
    assert payload["protocol"]["transitions"] == [
        {
            "from": "review",
            "to": "review",
            "on": "retry",
            "alias": "Retry Loop",
            "key": "retry-loop",
        },
        {
            "from": "review",
            "to": "decision",
            "on": "accept",
            "alias": "Accept Gate",
            "key": "accept-gate",
        },
    ]


def test_protocol_edit_updates_existing_protocol_from_file(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(tmp_path / ".btwin"))
    create_path = tmp_path / "create.yaml"
    create_path.write_text(_authoring_protocol_yaml(), encoding="utf-8")
    create_result = runner.invoke(
        app,
        ["protocol", "create", "--file", str(create_path), "--json"],
    )
    assert create_result.exit_code == 0, create_result.output

    edit_path = tmp_path / "edit.yaml"
    edit_path.write_text(_authoring_protocol_yaml().replace("Authoring-first review loop", "Updated loop"), encoding="utf-8")

    result = runner.invoke(
        app,
        ["protocol", "edit", "review-loop", "--file", str(edit_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["saved"] is True
    assert payload["name"] == "review-loop"
    assert payload["protocol"]["description"] == "Updated loop"


def test_protocol_preview_shows_authoring_summary_and_compiled_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(tmp_path / ".btwin"))
    protocol_path = tmp_path / "review-loop.yaml"
    protocol_path.write_text(_authoring_protocol_yaml(), encoding="utf-8")

    result = runner.invoke(
        app,
        ["protocol", "preview", "--file", str(protocol_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["source"] == {"kind": "file", "file": str(protocol_path)}
    assert payload["authoring"] == {
        "name": "review-loop",
        "phase_count": 2,
        "gate_count": 1,
        "outcome_policy_count": 1,
    }
    assert payload["compiled"]["outcomes"] == ["retry", "accept"]
    assert payload["compiled"]["phases"][0]["gate"] == "review-gate"


def test_protocol_create_attached_uses_shared_api(monkeypatch, tmp_path):
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(main, "_get_config", lambda: BTwinConfig(runtime=RuntimeConfig(mode="attached")))
    protocol_path = tmp_path / "review-loop.yaml"
    protocol_path.write_text(_authoring_protocol_yaml(), encoding="utf-8")

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "name": "review-loop",
            "description": "Authoring-first review loop",
            "phases": [
                {"name": "review", "actions": ["contribute"], "gate": "review-gate", "outcome_policy": "review-outcomes"},
                {"name": "decision", "actions": ["decide"], "decided_by": "user"},
            ],
            "gates": [{"name": "review-gate", "authoring_only": True, "routes": []}],
            "outcome_policies": [{"name": "review-outcomes", "authoring_only": True, "emitters": ["reviewer", "user"], "actions": ["decide"], "outcomes": ["retry", "accept"]}],
            "transitions": [{"from": "review", "to": "review", "on": "retry"}],
            "outcomes": ["retry", "accept"],
        }

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)

    result = runner.invoke(app, ["protocol", "create", "--file", str(protocol_path), "--json"])

    assert result.exit_code == 0, result.output
    assert calls == [("/api/protocols", _parse_json_output(json.dumps(main.load_protocol_yaml(protocol_path))))]


def test_protocol_edit_attached_uses_shared_api_put(monkeypatch, tmp_path):
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(main, "_get_config", lambda: BTwinConfig(runtime=RuntimeConfig(mode="attached")))
    protocol_path = tmp_path / "review-loop.yaml"
    protocol_path.write_text(_authoring_protocol_yaml(), encoding="utf-8")

    def fake_attached_put(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "name": "review-loop",
            "description": "Authoring-first review loop",
            "phases": [
                {"name": "review", "actions": ["contribute"], "gate": "review-gate", "outcome_policy": "review-outcomes"},
                {"name": "decision", "actions": ["decide"], "decided_by": "user"},
            ],
            "gates": [{"name": "review-gate", "authoring_only": True, "routes": []}],
            "outcome_policies": [{"name": "review-outcomes", "authoring_only": True, "emitters": ["reviewer", "user"], "actions": ["decide"], "outcomes": ["retry", "accept"]}],
            "transitions": [{"from": "review", "to": "review", "on": "retry"}],
            "outcomes": ["retry", "accept"],
        }

    monkeypatch.setattr(main, "_attached_api_put_or_exit", fake_attached_put)

    result = runner.invoke(app, ["protocol", "edit", "review-loop", "--file", str(protocol_path), "--json"])

    assert result.exit_code == 0, result.output
    assert calls == [("/api/protocols/review-loop", _parse_json_output(json.dumps(main.load_protocol_yaml(protocol_path))))]


def test_protocol_preview_attached_uses_shared_api(monkeypatch):
    calls: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(main, "_get_config", lambda: BTwinConfig(runtime=RuntimeConfig(mode="attached")))

    def fake_attached_get(path: str, params: dict | None = None):
        calls.append((path, params))
        return {
            "source": {"kind": "store", "name": "review-loop"},
            "authoring": {
                "name": "review-loop",
                "phase_count": 2,
                "gate_count": 1,
                "outcome_policy_count": 1,
            },
            "compiled": {
                "name": "review-loop",
                "phases": [{"name": "review", "actions": ["contribute"], "gate": "review-gate"}],
                "outcomes": ["retry", "accept"],
            },
        }

    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)

    result = runner.invoke(app, ["protocol", "preview", "review-loop", "--json"])

    assert result.exit_code == 0, result.output
    assert calls == [("/api/protocols/review-loop/preview", None)]
