from btwin_cli import mcp_proxy


def test_get_guidelines_reads_utf8_text(tmp_path, monkeypatch):
    guidelines = "Use btwin carefully \u2014 even on Windows.\n"
    (tmp_path / "guidelines.md").write_text(guidelines, encoding="utf-8")
    monkeypatch.setattr(mcp_proxy, "_data_dir", tmp_path)

    assert mcp_proxy.btwin_get_guidelines() == guidelines
