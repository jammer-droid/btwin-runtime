import json

from typer.testing import CliRunner

from btwin_cli.main import app

runner = CliRunner()


def test_protocol_validate_accepts_authoring_only_protocol_yaml(tmp_path):
    protocol_path = tmp_path / "review-loop.yaml"
    protocol_path.write_text(
        "\n".join(
            [
                "name: review-loop",
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
                "      - outcome: accept",
                "        target_phase: decision",
                "outcome_policies:",
                "  - name: review-outcomes",
                "    emitters: [reviewer, user]",
                "    actions: [decide]",
                "    outcomes: [retry, accept]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["protocol", "validate", "--file", str(protocol_path), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload == {
        "valid": True,
        "file": str(protocol_path),
        "name": "review-loop",
        "description": "",
        "phase_count": 2,
    }
