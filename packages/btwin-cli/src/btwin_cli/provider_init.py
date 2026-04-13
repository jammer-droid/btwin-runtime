"""Provider bootstrap helpers for `btwin init`."""

from __future__ import annotations

import copy
import json
import shutil
from importlib.resources import files
from pathlib import Path


def available_provider_names() -> list[str]:
    return ["codex"]


def provider_display_name(provider_name: str) -> str:
    return {"codex": "Codex"}.get(provider_name, provider_name)


def validate_provider_cli(provider_name: str) -> str:
    command = {"codex": "codex"}[provider_name]
    resolved = shutil.which(command)
    if resolved is None:
        raise RuntimeError(
            f"{provider_display_name(provider_name)} CLI not found in PATH. "
            f"Install `{command}` first, then run `btwin init` again."
        )
    return resolved


def _provider_seed_path(provider_name: str) -> Path:
    return Path(files("btwin_cli").joinpath("provider_seeds", f"{provider_name}.json"))


def build_provider_config(provider_name: str) -> dict:
    if provider_name not in available_provider_names():
        raise ValueError(f"Unsupported provider: {provider_name}")
    return copy.deepcopy(
        json.loads(_provider_seed_path(provider_name).read_text(encoding="utf-8"))
    )


def write_provider_config(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)
