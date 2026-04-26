from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (_repo_root() / path).read_text(encoding="utf-8")


def test_global_guidelines_describe_protocol_authoring_and_delegation_resume_surfaces():
    guidelines = _read("packages/btwin-cli/src/btwin_cli/global/guidelines.md")

    assert "btwin protocol scaffold" in guidelines
    assert "btwin protocol validate --file" in guidelines
    assert "btwin protocol preview --file" in guidelines
    assert "role_fulfillment" in guidelines
    assert "subagent_profiles" in guidelines
    assert "delegate wait/status/respond" in guidelines
    assert "target_role" in guidelines
    assert "resolved_agent" in guidelines
    assert "required_action" in guidelines
    assert "expected_output" in guidelines
    assert "managed_agent_subagent" in guidelines
    assert "tool policy is declared" in guidelines


def test_scenario_smoke_skill_mentions_scaffolded_managed_subagent_flow():
    skill = _read("packages/btwin-cli/src/btwin_cli/skills/bt-scenario-smoke/SKILL.md")

    assert "btwin protocol scaffold" in skill
    assert "managed_agent_subagent" in skill
    assert "scaffold -> validate -> preview -> create" in skill
    assert "executor metadata" in skill
    assert "tool policy remains declared" in skill
