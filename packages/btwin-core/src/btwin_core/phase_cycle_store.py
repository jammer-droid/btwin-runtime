"""Persist runtime-only phase cycle state per thread."""

from __future__ import annotations

import json
from pathlib import Path

from btwin_core.phase_cycle import PhaseCycleState


class PhaseCycleStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._dir = data_dir / "runtime" / "phase-cycles"

    def read(self, thread_id: str) -> PhaseCycleState | None:
        path = self._path(thread_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return PhaseCycleState.model_validate(payload)

    def write(self, state: PhaseCycleState) -> PhaseCycleState:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(state.thread_id).write_text(
            json.dumps(state.model_dump(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return state

    def start_cycle(
        self,
        *,
        thread_id: str,
        phase_name: str,
        procedure_steps: list[str] | None = None,
    ) -> PhaseCycleState:
        state = PhaseCycleState.start(
            thread_id=thread_id,
            phase_name=phase_name,
            procedure_steps=procedure_steps,
        )
        return self.write(state)

    def finish_cycle(
        self,
        *,
        thread_id: str,
        gate_outcome: str,
        next_phase: str | None,
    ) -> PhaseCycleState:
        current = self.read(thread_id)
        if current is None:
            raise ValueError(f"Phase cycle state not found for thread: {thread_id}")
        return self.write(current.finish_cycle(gate_outcome=gate_outcome, next_phase=next_phase))

    def delete_thread(self, thread_id: str) -> None:
        path = self._path(thread_id)
        if path.exists():
            path.unlink()

    def _path(self, thread_id: str) -> Path:
        return self._dir / f"{thread_id}.json"
