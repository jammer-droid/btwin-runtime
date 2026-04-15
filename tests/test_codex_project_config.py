from pathlib import Path

import btwin_cli.main as main


def test_write_codex_project_config_removes_legacy_root_level_btwin_args(tmp_path):
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'args = ["mcp-proxy", "--project", "old-project"]\n'
        'args = ["mcp-proxy", "--project", "older-project"]\n'
        "[mcp_servers.btwin]\n"
        'command = "btwin"\n'
        'args = ["mcp-proxy", "--project", "stale-project"]\n',
        encoding="utf-8",
    )

    main._write_codex_project_config(config_path, "fresh-project")

    written = config_path.read_text(encoding="utf-8")
    assert 'args = ["mcp-proxy", "--project", "old-project"]' not in written
    assert 'args = ["mcp-proxy", "--project", "older-project"]' not in written
    assert 'args = ["mcp-proxy", "--project", "fresh-project"]' in written


def test_write_codex_project_hooks_creates_btwin_hook_commands(tmp_path):
    hooks_path = tmp_path / ".codex" / "hooks.json"

    main._write_codex_project_hooks(hooks_path)

    written = hooks_path.read_text(encoding="utf-8")
    assert '"SessionStart"' in written
    assert '"UserPromptSubmit"' in written
    assert '"Stop"' in written
    assert "workflow hook" in written
