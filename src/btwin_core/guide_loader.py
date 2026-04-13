"""Guide loader for task scaffolding guides."""

from __future__ import annotations

import re
from pathlib import Path


class GuideLoader:
    def __init__(self, data_dir: Path) -> None:
        self._guides_dir = data_dir / "guides"

    def list_guides(self) -> list[dict]:
        if not self._guides_dir.is_dir():
            return []
        guides = []
        for f in sorted(self._guides_dir.iterdir()):
            if f.suffix == ".md":
                meta = self._parse_frontmatter(f)
                if meta:
                    guides.append(meta)
        return guides

    def get_guide(self, guide_id: str) -> dict | None:
        if not self._guides_dir.is_dir():
            return None
        for f in self._guides_dir.iterdir():
            if f.suffix == ".md":
                meta = self._parse_frontmatter(f)
                if meta and meta["id"] == guide_id:
                    content = f.read_text(encoding="utf-8")
                    body = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL).strip()
                    return {**meta, "content": body}
        return None

    def get_guides_content(self, guide_ids: list[str]) -> str:
        parts = []
        for gid in guide_ids:
            guide = self.get_guide(gid)
            if guide:
                parts.append(f"## Guide: {guide['name']}\n\n{guide['content']}")
        return "\n\n---\n\n".join(parts)

    def _parse_frontmatter(self, path: Path) -> dict | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not match:
            return None
        meta = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                meta[key.strip()] = val.strip()
        return meta if "id" in meta else None
