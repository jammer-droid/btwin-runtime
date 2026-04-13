"""Pipeline template loader."""

from __future__ import annotations

import json
from pathlib import Path


class PipelineLoader:
    def __init__(self, data_dir: Path) -> None:
        self._pipelines_dir = data_dir / "pipelines"

    def list_templates(self) -> list[dict]:
        if not self._pipelines_dir.is_dir():
            return []
        templates = []
        for f in sorted(self._pipelines_dir.iterdir()):
            if f.suffix == ".json":
                try:
                    templates.append(json.loads(f.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
        return templates

    def get_template(self, template_id: str) -> dict | None:
        path = self._pipelines_dir / f"{template_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
