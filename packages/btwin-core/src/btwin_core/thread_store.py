# src/btwin/core/thread_store.py
"""Thread store — manages collaboration threads, messages, and contributions."""

from __future__ import annotations

import logging
import random
import shutil
import string
from datetime import datetime, timezone
from pathlib import Path

import yaml

from btwin_core.phase_cycle_store import PhaseCycleStore

logger = logging.getLogger(__name__)


def _generate_thread_id() -> str:
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y%m%d")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"thread-{date_part}-{suffix}"


def _generate_message_id() -> str:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%H%M%S%f")
    suffix = "".join(random.choices(string.digits, k=3))
    return f"msg-{ts}{suffix}"


def _generate_contribution_id() -> str:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%H%M%S%f")
    suffix = "".join(random.choices(string.digits, k=3))
    return f"contrib-{ts}{suffix}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ThreadStore:
    """Manages thread lifecycle, messages, and contributions on disk."""

    def __init__(self, threads_dir: Path) -> None:
        self._dir = threads_dir

    @property
    def data_dir(self) -> Path:
        """Return the project data directory that contains the threads directory."""
        return self._dir.parent

    def workflow_event_log_path(self, thread_id: str) -> Path:
        """Return the canonical workflow event log path for one thread."""
        return self._dir / thread_id / "workflow-events.jsonl"

    # -- Lifecycle --

    def create_thread(
        self,
        topic: str,
        protocol: str,
        participants: list[str] | None = None,
        initial_phase: str | None = None,
        locale: dict[str, object] | None = None,
    ) -> dict:
        thread_id = _generate_thread_id()
        now = _iso_now()
        participant_list = []
        if participants:
            for name in participants:
                participant_list.append({"name": name, "joined_at": now})

        meta = {
            "thread_id": thread_id,
            "topic": topic,
            "protocol": protocol,
            "status": "active",
            "created_at": now,
            "participants": participant_list,
            "current_phase": initial_phase,
            "interaction_mode": "discuss",
            "phase_participants": list(participants or []),
        }
        if locale is not None:
            meta["locale"] = dict(locale)

        thread_dir = self._dir / thread_id
        thread_dir.mkdir(parents=True)
        (thread_dir / "contributions").mkdir()
        (thread_dir / "messages").mkdir()
        self._save_meta(thread_id, meta)
        return meta

    def join_thread(self, thread_id: str, agent_name: str) -> dict | None:
        meta = self._load_meta(thread_id)
        if meta is None:
            return None
        names = [p["name"] for p in meta["participants"]]
        if agent_name not in names:
            meta["participants"].append({"name": agent_name, "joined_at": _iso_now()})
            self._save_meta(thread_id, meta)
        return meta

    def close_thread(
        self,
        thread_id: str,
        summary: str,
        decision: str | None = None,
    ) -> dict | None:
        meta = self._load_meta(thread_id)
        if meta is None:
            return None
        meta["status"] = "completed"
        meta["closed_at"] = _iso_now()
        self._save_meta(thread_id, meta)

        decision_content = f"## Summary\n\n{summary}"
        if decision:
            decision_content += f"\n\n## Decision\n\n{decision}"
        (self._dir / thread_id / "decision.md").write_text(decision_content, encoding="utf-8")
        return meta

    def get_thread(self, thread_id: str) -> dict | None:
        return self._load_meta(thread_id)

    def advance_phase(self, thread_id: str, next_phase: str) -> dict | None:
        """Advance thread to the next phase. Returns None if thread not found or closed."""
        meta = self._load_meta(thread_id)
        if meta is None:
            return None
        if meta.get("status") != "active":
            return None
        meta["current_phase"] = next_phase
        meta["phase_participants"] = [p["name"] for p in meta.get("participants", [])]
        self._save_meta(thread_id, meta)
        return meta

    def set_interaction_mode(self, thread_id: str, interaction_mode: str) -> dict | None:
        """Persist the current interaction mode for the thread."""
        meta = self._load_meta(thread_id)
        if meta is None:
            return None
        if meta.get("status") != "active":
            return None
        meta["interaction_mode"] = interaction_mode
        self._save_meta(thread_id, meta)
        return meta

    def get_status(self, thread_id: str) -> dict | None:
        """Return current phase and per-agent status."""
        meta = self._load_meta(thread_id)
        if meta is None:
            return None
        current_phase = meta.get("current_phase")
        contributions = self.list_contributions(thread_id, phase=current_phase) if current_phase else []
        contributed_agents = {c["agent"] for c in contributions}

        agents = []
        for p in meta.get("participants", []):
            name = p["name"]
            if name in contributed_agents:
                status = "contributed"
            else:
                status = "joined"
            agents.append({
                "name": name,
                "status": status,
                "joined_at": p.get("joined_at"),
            })

        return {
            "thread_id": thread_id,
            "current_phase": current_phase,
            "agents": agents,
        }

    def list_inbox(self, thread_id: str, agent_name: str) -> list[dict] | None:
        """Return unacked messages relevant to one agent within a thread."""
        meta = self._load_meta(thread_id)
        if meta is None:
            return None

        participant_names = {p["name"] for p in meta.get("participants", [])}
        if agent_name not in participant_names:
            return None

        inbox = []
        for message in self.list_messages(thread_id):
            if message.get("from") == agent_name:
                continue
            if agent_name in message.get("acked_by", []):
                continue

            delivery_mode = message.get("delivery_mode", "auto")
            target_agents = message.get("target_agents", [])
            if delivery_mode == "direct" and agent_name not in target_agents:
                continue
            if delivery_mode in {"broadcast", "auto"} or (
                delivery_mode == "direct" and agent_name in target_agents
            ):
                inbox.append(message)

        return inbox

    def get_agent_status(self, thread_id: str, agent_name: str) -> dict | None:
        """Return compact per-agent thread status including pending inbox messages."""
        meta = self._load_meta(thread_id)
        if meta is None:
            return None

        status = self.get_status(thread_id)
        inbox = self.list_inbox(thread_id, agent_name)
        if status is None or inbox is None:
            return None

        participant_status = next(
            (agent["status"] for agent in status.get("agents", []) if agent["name"] == agent_name),
            None,
        )
        pending_messages = [
            {
                "message_id": message["message_id"],
                "from": message["from"],
                "tldr": message["tldr"],
                "delivery_mode": message["delivery_mode"],
                "created_at": message["created_at"],
            }
            for message in inbox
        ]
        return {
            "thread_id": thread_id,
            "agent": agent_name,
            "current_phase": meta.get("current_phase"),
            "interaction_mode": meta.get("interaction_mode"),
            "participant_status": participant_status,
            "pending_message_count": len(pending_messages),
            "pending_messages": pending_messages,
        }

    def list_threads(self, status: str | None = None) -> list[dict]:
        if not self._dir.exists():
            return []
        results = []
        for d in sorted(self._dir.iterdir()):
            if not d.is_dir():
                continue
            meta = self._load_meta(d.name)
            if meta is None:
                continue
            if status and meta.get("status") != status:
                continue
            results.append(meta)
        return results

    def gc_closed_threads(
        self,
        *,
        mailbox_store,
        gc_log,
        max_closed_threads: int,
    ) -> dict[str, int]:
        if max_closed_threads < 0:
            raise ValueError("max_closed_threads must be >= 0")

        closed_threads: list[tuple[datetime, dict]] = []
        for meta in self.list_threads(status="completed"):
            closed_threads.append((self._closed_thread_sort_key(meta), meta))

        closed_threads.sort(key=lambda item: item[0], reverse=True)
        stale_threads = closed_threads[max_closed_threads:]
        stale_thread_ids = {
            meta["thread_id"]
            for _, meta in stale_threads
            if isinstance(meta.get("thread_id"), str)
        }
        phase_cycle_store = PhaseCycleStore(self.data_dir)
        deleted_threads = 0
        for _, meta in stale_threads:
            thread_id = meta.get("thread_id")
            if not isinstance(thread_id, str):
                continue
            thread_dir = self._dir / thread_id
            if thread_dir.exists():
                shutil.rmtree(thread_dir)
                deleted_threads += 1
            phase_cycle_store.delete_thread(thread_id)
            gc_log.append_event(
                {
                    "thread_id": thread_id,
                    "deleted_at": _iso_now(),
                    "reason": "lru_closed_thread_gc",
                    "last_status": meta.get("status"),
                }
            )

        deleted_reports = mailbox_store.delete_reports_for_threads(stale_thread_ids)
        return {
            "deleted_threads": deleted_threads,
            "deleted_reports": deleted_reports,
        }

    # -- Messages --

    def send_message(
        self,
        thread_id: str,
        from_agent: str,
        content: str,
        tldr: str,
        client_message_id: str | None = None,
        msg_type: str = "message",
        reply_to: str | None = None,
        delivery_mode: str = "auto",
        target_agents: list[str] | None = None,
        routing_source: str = "fallback",
        routing_reason: str = "",
        message_phase: str | None = None,
        state_affecting: bool = True,
    ) -> dict | None:
        meta = self._load_meta(thread_id)
        if meta is None:
            return None
        if meta.get("status") != "active":
            return None

        normalized_targets = list(target_agents or [])

        message_id = _generate_message_id()
        now = _iso_now()
        msg = {
            "message_id": message_id,
            "thread_id": thread_id,
            "from": from_agent,
            "msg_type": msg_type,
            "reply_to": reply_to,
            "client_message_id": client_message_id,
            "tldr": tldr,
            "delivery_mode": delivery_mode,
            "target_agents": normalized_targets,
            "routing_source": routing_source,
            "routing_reason": routing_reason,
            "message_phase": message_phase,
            "state_affecting": state_affecting,
            "created_at": now,
            "acked_by": [],
        }

        filename = f"{now.replace(':', '').replace('+', '_')}-{from_agent}.md"
        msg_path = self._dir / thread_id / "messages" / filename
        frontmatter = yaml.dump(msg, allow_unicode=True, sort_keys=False)
        msg_path.write_text(f"---\n{frontmatter}---\n\n{content}\n", encoding="utf-8")
        return msg

    def list_messages(
        self,
        thread_id: str,
        since: str | None = None,
    ) -> list[dict]:
        msg_dir = self._dir / thread_id / "messages"
        if not msg_dir.exists():
            return []

        messages = []
        found_since = since is None
        for p in sorted(msg_dir.glob("*.md")):
            parsed = self._parse_message(p)
            if parsed is None:
                continue
            if not found_since:
                if parsed["message_id"] == since:
                    found_since = True
                continue
            messages.append(parsed)
        return messages

    def list_recent_messages(self, thread_id: str, limit: int = 5) -> list[dict]:
        """Return the newest messages for quick interactive thread context."""
        if limit <= 0:
            return []
        messages = self.list_messages(thread_id)
        return messages[-limit:]

    def ack_message(self, thread_id: str, message_id: str, agent_name: str) -> bool:
        msg_dir = self._dir / thread_id / "messages"
        if not msg_dir.exists():
            return False
        for p in msg_dir.glob("*.md"):
            parsed = self._parse_message(p)
            if parsed and parsed["message_id"] == message_id:
                if agent_name not in parsed["acked_by"]:
                    parsed["acked_by"].append(agent_name)
                    content = self._extract_body(p)
                    frontmatter = yaml.dump(parsed, allow_unicode=True, sort_keys=False)
                    p.write_text(f"---\n{frontmatter}---\n\n{content}\n", encoding="utf-8")
                return True
        return False

    # -- Contributions --

    def submit_contribution(
        self,
        thread_id: str,
        agent_name: str,
        phase: str,
        content: str,
        tldr: str,
    ) -> dict | None:
        meta = self._load_meta(thread_id)
        if meta is None:
            return None
        if meta.get("status") != "active":
            return None

        previous = self.list_contributions(
            thread_id, phase=phase, participant=agent_name, include_history=False,
        )
        latest_prev = previous[0] if previous else None

        contribution_id = _generate_contribution_id()
        now = _iso_now()
        contrib = {
            "contribution_id": contribution_id,
            "agent": agent_name,
            "phase": phase,
            "tldr": tldr,
            "supersedes": latest_prev["contribution_id"] if latest_prev else None,
            "created_at": now,
        }

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"{agent_name}-{phase}-{timestamp}.md"
        contrib_path = self._dir / thread_id / "contributions" / filename
        frontmatter = yaml.dump(contrib, allow_unicode=True, sort_keys=False)
        contrib_path.write_text(f"---\n{frontmatter}---\n\n{content}\n", encoding="utf-8")
        return contrib

    def list_contributions(
        self,
        thread_id: str,
        phase: str | None = None,
        participant: str | None = None,
        include_history: bool = False,
    ) -> list[dict]:
        contrib_dir = self._dir / thread_id / "contributions"
        if not contrib_dir.exists():
            return []

        results = []
        for p in sorted(contrib_dir.glob("*.md")):
            parsed = self._parse_contribution(p)
            if parsed is None:
                continue
            if phase and parsed.get("phase") != phase:
                continue
            if participant and parsed.get("agent") != participant:
                continue
            results.append(parsed)

        results.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        if include_history:
            return results

        latest_by_slot: dict[tuple[str, str], dict] = {}
        for item in results:
            key = (item["agent"], item["phase"])
            if key not in latest_by_slot:
                latest_by_slot[key] = item
        return list(latest_by_slot.values())

    # -- Internal --

    def _meta_path(self, thread_id: str) -> Path:
        return self._dir / thread_id / "thread.yaml"

    def _save_meta(self, thread_id: str, meta: dict) -> None:
        path = self._meta_path(thread_id)
        path.write_text(yaml.dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def _load_meta(self, thread_id: str) -> dict | None:
        path = self._meta_path(thread_id)
        if not path.exists():
            return None
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load thread metadata: %s", path)
            return None

    def _parse_message(self, path: Path) -> dict | None:
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.startswith("---\n"):
                return None
            parts = raw.split("---\n", 2)
            if len(parts) < 3:
                return None
            meta = yaml.safe_load(parts[1])
            if not isinstance(meta, dict):
                return None
            meta["_content"] = parts[2].strip()
            return meta
        except Exception:
            return None

    def _parse_contribution(self, path: Path) -> dict | None:
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.startswith("---\n"):
                return None
            parts = raw.split("---\n", 2)
            if len(parts) < 3:
                return None
            meta = yaml.safe_load(parts[1])
            if not isinstance(meta, dict):
                return None
            meta["_content"] = parts[2].strip()
            return meta
        except Exception:
            return None

    def _extract_body(self, path: Path) -> str:
        raw = path.read_text(encoding="utf-8")
        parts = raw.split("---\n", 2)
        return parts[2].strip() if len(parts) >= 3 else ""

    def _closed_thread_sort_key(self, meta: dict) -> datetime:
        for field in ("last_accessed_at", "closed_at", "created_at"):
            value = meta.get(field)
            parsed = self._parse_datetime(value)
            if parsed is not None:
                return parsed
        return datetime.min.replace(tzinfo=timezone.utc)

    def _parse_datetime(self, value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
