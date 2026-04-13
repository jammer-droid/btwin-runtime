"""Build phase context for agents from ThreadStore + ProtocolStore data."""

from __future__ import annotations

from btwin_core.protocol_store import ProtocolStore
from btwin_core.thread_store import ThreadStore


class PhaseContextBuilder:
    def __init__(self, thread_store: ThreadStore, protocol_store: ProtocolStore) -> None:
        self._threads = thread_store
        self._protocols = protocol_store

    def build(self, thread_id: str) -> dict | None:
        meta = self._threads.get_thread(thread_id)
        if meta is None:
            return None

        proto = self._protocols.get_protocol(meta["protocol"])
        if proto is None:
            return None

        current_phase_name = meta.get("current_phase")
        phase_def = next((phase for phase in proto.phases if phase.name == current_phase_name), None)

        prev_contributions: list[dict] = []
        if current_phase_name:
            phase_names = [phase.name for phase in proto.phases]
            idx = phase_names.index(current_phase_name) if current_phase_name in phase_names else -1
            if idx > 0:
                prev_phase = phase_names[idx - 1]
                prev_contributions = self._threads.list_contributions(thread_id, phase=prev_phase)

        return {
            "thread_id": thread_id,
            "current_phase": phase_def.model_dump() if phase_def else None,
            "previous_phase_contributions": prev_contributions,
            "phase_participants": meta.get("phase_participants", []),
        }
