"""Project-local runtime binding store for the current thread/agent context."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    agent_name: str
    bound_at: str


class RuntimeBindingState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding: RuntimeBinding | None = None
    binding_error: str | None = None

    @property
    def bound(self) -> bool:
        return self.binding is not None


class RuntimeBindingStore:
    """Persist the current runtime binding under the project .btwin area."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "runtime" / "binding.json"

    def read_state(self) -> RuntimeBindingState:
        if not self.file_path.exists():
            return RuntimeBindingState()

        try:
            raw = self.file_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            return RuntimeBindingState(binding=RuntimeBinding.model_validate(data))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Failed to load runtime binding: %s", self.file_path, exc_info=True)
            return RuntimeBindingState(
                binding_error=f"Failed to load runtime binding: {exc.__class__.__name__}: {exc}",
            )

    def write(self, binding: RuntimeBinding) -> RuntimeBinding:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(binding.model_dump(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.file_path.parent,
                prefix="binding-",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tmp_file.write(payload)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            tmp_path.replace(self.file_path)
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
        return binding

    def bind(self, thread_id: str, agent_name: str) -> RuntimeBinding:
        binding = RuntimeBinding(thread_id=thread_id, agent_name=agent_name, bound_at=_now_iso())
        return self.write(binding)

    def clear(self) -> RuntimeBindingState:
        current = self.read_state()
        if self.file_path.exists():
            self.file_path.unlink()
        return current
