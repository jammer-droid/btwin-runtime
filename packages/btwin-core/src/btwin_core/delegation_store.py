"""JSONL-backed store for delegation state snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from btwin_core.delegation_state import DelegationState


class DelegationStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "runtime" / "delegation-state.jsonl"

    def read(self, thread_id: str) -> DelegationState | None:
        if not self.file_path.exists():
            return None

        for state in reversed(self._read_states()):
            if state.thread_id == thread_id:
                return state
        return None

    def write(self, state: DelegationState) -> DelegationState:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(state.model_dump(), ensure_ascii=False, sort_keys=True) + "\n")
        return state

    def list_states(self) -> list[DelegationState]:
        states: list[DelegationState] = []
        seen_thread_ids: set[str] = set()

        for state in reversed(self._read_states()):
            if state.thread_id in seen_thread_ids:
                continue
            seen_thread_ids.add(state.thread_id)
            states.append(state)

        return states

    def delete(self, thread_id: str) -> bool:
        if not self.file_path.exists():
            return False

        kept_lines: list[str] = []
        deleted = False
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                kept_lines.append(line)
                continue
            if isinstance(payload, dict) and payload.get("thread_id") == thread_id:
                deleted = True
                continue
            kept_lines.append(line)

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        rewritten = "\n".join(kept_lines)
        if rewritten:
            rewritten += "\n"
        self.file_path.write_text(rewritten, encoding="utf-8")
        return deleted

    def _read_states(self) -> list[DelegationState]:
        if not self.file_path.exists():
            return []

        states: list[DelegationState] = []
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                states.append(DelegationState.model_validate(payload))
            except (json.JSONDecodeError, ValidationError):
                continue
        return states
