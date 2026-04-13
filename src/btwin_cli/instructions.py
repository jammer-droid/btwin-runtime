"""Build MCP server instructions from guidelines."""

from __future__ import annotations

from pathlib import Path

from btwin_core.config import resolve_data_dir


def _extract_section(text: str, heading: str) -> str:
    """Extract a markdown section by heading (##)."""
    marker = f"## {heading}"
    start = text.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    # Find next ## heading
    next_heading = text.find("\n## ", start)
    if next_heading == -1:
        return text[start:].strip()
    return text[start:next_heading].strip()


def build_instructions(data_dir: Path | None = None) -> str:
    """Build compact MCP instructions from guidelines.md.

    Returns key rules that every LLM agent must follow when using btwin tools.
    Full guidelines available via btwin_get_guidelines tool.
    """
    guidelines_dir = data_dir or resolve_data_dir()
    guidelines_path = guidelines_dir / "guidelines.md"
    if not guidelines_path.exists():
        return _FALLBACK_INSTRUCTIONS

    text = guidelines_path.read_text(encoding="utf-8")

    sections = []
    for heading in (
        "Standard Frontmatter Schema",
        "TLDR Writing Rules",
        "Search → Full Content Workflow",
        "Recording Rules",
    ):
        content = _extract_section(text, heading)
        if content:
            sections.append(f"## {heading}\n\n{content}")

    if not sections:
        return _FALLBACK_INSTRUCTIONS

    header = (
        "# B-TWIN MCP — Key Rules\n\n"
        "> These rules apply to ALL btwin tool calls. "
        "Full guidelines: call `btwin_get_guidelines`.\n"
    )
    return header + "\n\n" + "\n\n".join(sections)


_FALLBACK_INSTRUCTIONS = """\
# B-TWIN MCP — Key Rules

All record tools (btwin_record, btwin_convo_record, btwin_end_session) require a `tldr` parameter.
TLDR: 1-3 sentences, max 200 chars, concrete facts and searchable keywords.
Search returns TLDR + record_id. Use btwin://record/{record_id} for full content.
Full guidelines: call `btwin_get_guidelines`.
"""
