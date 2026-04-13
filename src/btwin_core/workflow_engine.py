"""WorkflowEngine — sequential task orchestration via btwin entries.

Stores all workflow data as markdown files with YAML frontmatter
under entries/workflow/{date}/.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from btwin_core.agent_store import AgentStore
from btwin_core.event_bus import EventBus, SSEEvent
from btwin_core.storage import Storage
from btwin_core.workflow_gate import (
    validate_task_transition,
    validate_workflow_transition,
)


def _generate_id(prefix: str) -> str:
    """Generate a short unique id: {prefix}-{uuid4[:12]}."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class WorkflowEngine:
    """Orchestrates sequential workflow execution.

    Each workflow and task is stored as a separate .md file under
    entries/workflow/{date}/{record_id}.md with YAML frontmatter.

    Tags encode workflow metadata:
      wf-type:epic | wf-type:task
      wf-id:{workflow_id}
      wf-status:{status}

    Linkage via frontmatter fields:
      derived_from: parent record_id (task -> workflow)
      related_records: sibling links
    """

    def __init__(self, storage: Storage, event_bus: EventBus | None = None) -> None:
        self._storage = storage
        self._event_bus = event_bus

    def _publish(self, event_type: str, resource_id: str) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(SSEEvent(type=event_type, resource_id=resource_id))

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _workflow_dir(self, date: str | None = None) -> Path:
        return self._storage.entries_dir / "workflow" / (date or _today())

    def _write_entry(self, record_id: str, frontmatter: dict, body: str, date: str | None = None) -> Path:
        """Write a markdown entry with YAML frontmatter."""
        d = date or _today()
        out_dir = self._workflow_dir(d)
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / f"{record_id}.md"
        fm_text = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
        file_path.write_text(f"---\n{fm_text}\n---\n\n{body}\n")
        return file_path

    def _read_all_workflow_entries(self) -> list[dict]:
        """Read all .md files under entries/workflow/ and return parsed frontmatter+body."""
        wf_root = self._storage.entries_dir / "workflow"
        if not wf_root.exists():
            return []

        entries = []
        for md_file in sorted(wf_root.rglob("*.md")):
            raw = md_file.read_text()
            if not raw.startswith("---\n"):
                continue
            parts = raw.split("---\n", 2)
            if len(parts) < 3:
                continue
            fm = yaml.safe_load(parts[1]) or {}
            body = parts[2].lstrip("\n")
            fm["_body"] = body
            fm["_path"] = str(md_file)
            entries.append(fm)
        return entries

    def _find_entry(self, record_id: str) -> dict | None:
        for entry in self._read_all_workflow_entries():
            if entry.get("record_id") == record_id:
                return entry
        return None

    def _update_entry_frontmatter(self, record_id: str, updates: dict) -> bool:
        """Update frontmatter fields of an existing entry on disk."""
        wf_root = self._storage.entries_dir / "workflow"
        if not wf_root.exists():
            return False

        for md_file in wf_root.rglob("*.md"):
            raw = md_file.read_text()
            if not raw.startswith("---\n"):
                continue
            parts = raw.split("---\n", 2)
            if len(parts) < 3:
                continue
            fm = yaml.safe_load(parts[1]) or {}
            if fm.get("record_id") != record_id:
                continue

            fm.update(updates)
            fm["last_updated_at"] = _now_iso()

            # Update wf-status tag if status changed
            if "tags" in fm and "status" in updates:
                new_tags = []
                for tag in fm["tags"]:
                    if tag.startswith("wf-status:"):
                        new_tags.append(f"wf-status:{updates['status']}")
                    else:
                        new_tags.append(tag)
                fm["tags"] = new_tags

            body = parts[2].lstrip("\n")
            fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
            md_file.write_text(f"---\n{fm_text}\n---\n\n{body}")
            return True

        return False

    def _add_timeline_event(self, record_id: str, event: str) -> None:
        """Append a timeline event to an entry's body."""
        wf_root = self._storage.entries_dir / "workflow"
        if not wf_root.exists():
            return

        for md_file in wf_root.rglob("*.md"):
            raw = md_file.read_text()
            if not raw.startswith("---\n"):
                continue
            parts = raw.split("---\n", 2)
            if len(parts) < 3:
                continue
            fm = yaml.safe_load(parts[1]) or {}
            if fm.get("record_id") != record_id:
                continue

            body = parts[2].lstrip("\n").rstrip("\n")
            timestamp = _now_iso()
            timeline_line = f"- [{timestamp}] {event}"
            if body:
                body = f"{body}\n{timeline_line}\n"
            else:
                body = f"{timeline_line}\n"

            fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
            md_file.write_text(f"---\n{fm_text}\n---\n\n{body}")
            return

    def _start_task(self, task_id: str, workflow_id: str, actual_model: str | None = None) -> dict | None:
        """Start a pending task: validate, update frontmatter, timeline, SSE.

        Returns task info dict or None if the task cannot be started.
        """
        entry = self._find_entry(task_id)
        if entry is None or entry.get("status") != "pending":
            return None

        decision = validate_task_transition("pending", "in_progress")
        if not decision.ok:
            return None

        updates: dict = {"status": "in_progress"}
        if actual_model is not None:
            updates["actual_model"] = actual_model

        self._update_entry_frontmatter(task_id, updates)

        agent = entry.get("assigned_agent")
        if actual_model:
            event_text = f"task '{entry['name']}' started (agent: {agent or 'unknown'}, model: {actual_model})"
        else:
            event_text = f"task '{entry['name']}' started (agent: {agent or 'unknown'})"

        self._add_timeline_event(workflow_id, event_text)
        self._publish("task_updated", task_id)

        return {
            "task_id": task_id,
            "name": entry["name"],
            "status": "in_progress",
            "order": entry.get("order", 0),
            "assigned_agent": agent,
            "actual_model": actual_model,
            "workflow_id": workflow_id,
        }

    # =========================================================================
    # Public API
    # =========================================================================

    def create_workflow(
        self,
        *,
        name: str,
        task_names: list[str],
        assigned_agents: list[str | None] | None = None,
        contributor: str | None = None,
    ) -> dict:
        """Create a workflow with sequential tasks.

        Returns dict with workflow_id, name, status, tasks.
        """
        now = _now_iso()
        date = _today()
        wf_id = _generate_id("wf")

        # Create workflow entry
        wf_fm = {
            "record_id": wf_id,
            "record_type": "workflow",
            "date": date,
            "created_at": now,
            "last_updated_at": now,
            "name": name,
            "status": "active",
            "tags": ["wf-type:epic", f"wf-id:{wf_id}", "wf-status:active"],
            "source_project": "_global",
            "tldr": name,
            "contributors": [contributor] if contributor else [],
        }

        task_ids = []
        tasks_info = []

        for i, task_name in enumerate(task_names, start=1):
            task_id = _generate_id("task")
            task_ids.append(task_id)

            agent = (
                assigned_agents[i - 1]
                if assigned_agents and i - 1 < len(assigned_agents)
                else None
            )

            task_fm = {
                "record_id": task_id,
                "record_type": "workflow",
                "date": date,
                "created_at": now,
                "last_updated_at": now,
                "name": task_name,
                "status": "pending",
                "order": i,
                "assigned_agent": agent,
                "tags": ["wf-type:task", f"wf-id:{wf_id}", "wf-status:pending"],
                "derived_from": wf_id,
                "source_project": "_global",
                "tldr": task_name,
                "contributors": [contributor] if contributor else [],
            }

            self._write_entry(task_id, task_fm, f"Task: {task_name}", date)

            tasks_info.append({
                "task_id": task_id,
                "name": task_name,
                "status": "pending",
                "order": i,
                "assigned_agent": agent,
            })

        # Store task_ids in workflow frontmatter
        wf_fm["task_ids"] = task_ids
        self._write_entry(wf_id, wf_fm, f"Workflow: {name}", date)

        # Record timeline event
        self._add_timeline_event(wf_id, f"workflow created with {len(task_names)} tasks")
        self._publish("workflow_updated", wf_id)

        return {
            "workflow_id": wf_id,
            "name": name,
            "status": "active",
            "tasks": tasks_info,
        }

    def list_tasks(self, workflow_id: str) -> list[dict]:
        """Return tasks for a workflow, sorted by order."""
        entries = self._read_all_workflow_entries()

        tasks = []
        for entry in entries:
            tags = entry.get("tags", [])
            if (
                f"wf-id:{workflow_id}" in tags
                and "wf-type:task" in tags
            ):
                tasks.append({
                    "task_id": entry["record_id"],
                    "name": entry["name"],
                    "status": entry["status"],
                    "order": entry.get("order", 0),
                    "assigned_agent": entry.get("assigned_agent"),
                    "actual_model": entry.get("actual_model"),
                    "attached_guides": entry.get("attached_guides", []),
                })

        tasks.sort(key=lambda t: t["order"])
        return tasks

    def start_next_task(self, workflow_id: str, actual_model: str | None = None) -> dict | None:
        """Start the next pending task (sequential: only if no task is in_progress).

        Returns the started task dict, or None if not possible.
        """
        tasks = self.list_tasks(workflow_id)

        # Sequential constraint: no task should be in_progress
        if any(t["status"] == "in_progress" for t in tasks):
            return None

        # Find first pending task and start it
        for t in tasks:
            if t["status"] == "pending":
                return self._start_task(t["task_id"], workflow_id, actual_model)

        # All tasks done
        return None

    def next_task_from_queue(
        self,
        agent_name: str,
        agent_store: AgentStore,
        actual_model: str | None = None,
    ) -> dict | None:
        """Pop the first queued task for an agent and start it.

        Returns task info dict or None if queue is empty or no task can start.
        """
        queue = agent_store.get_queue(agent_name)
        if not queue:
            return None

        for queued_item in queue:
            q_wf_id = queued_item["workflow_id"]
            q_task_id = queued_item["task_id"]

            # Check task exists and is pending; dequeue stale items
            task_entry = self._find_entry(q_task_id)
            if task_entry is None or task_entry.get("status") != "pending":
                agent_store.dequeue_task(agent_name, q_task_id)
                continue

            # Check sequential constraint: no in_progress task in this workflow
            wf_tasks = self.list_tasks(q_wf_id)
            if any(t["status"] == "in_progress" for t in wf_tasks):
                continue

            started = self._start_task(q_task_id, q_wf_id, actual_model)
            if started is None:
                continue

            agent_store.dequeue_task(agent_name, q_task_id)
            return started

        return None

    def complete_task(
        self,
        workflow_id: str,
        task_id: str,
        agent_store: AgentStore | None = None,
        agent_name: str | None = None,
        actual_model: str | None = None,
    ) -> dict | None:
        """Mark a task as done. Auto-completes workflow if all tasks are done.

        If agent_store and agent_name are provided, auto-starts the next
        queued task for this agent after completion.
        actual_model is passed through to the auto-advanced task.
        """
        task = self._find_entry(task_id)
        if task is None:
            return None

        decision = validate_task_transition(task["status"], "done")
        if not decision.ok:
            return None

        self._update_entry_frontmatter(task_id, {"status": "done"})
        self._add_timeline_event(
            workflow_id,
            f"task '{task['name']}' completed (agent: {task.get('assigned_agent') or 'unknown'})",
        )

        # Check if all tasks are done -> auto-complete workflow
        tasks = self.list_tasks(workflow_id)
        all_done = all(t["status"] == "done" for t in tasks)
        if all_done:
            wf_decision = validate_workflow_transition("active", "completed")
            if wf_decision.ok:
                self._update_entry_frontmatter(workflow_id, {"status": "completed"})
                self._add_timeline_event(workflow_id, "workflow auto-completed (all tasks done)")
        self._publish("task_updated", task_id)

        # Auto-advance: start next queued task if agent_store provided
        if agent_store is not None and agent_name is not None:
            queue = agent_store.get_queue(agent_name)
            for queued_item in queue:
                q_wf_id = queued_item["workflow_id"]
                q_task_id = queued_item["task_id"]
                # Check the task is pending; dequeue stale items
                q_task = self._find_entry(q_task_id)
                if q_task is None or q_task.get("status") != "pending":
                    agent_store.dequeue_task(agent_name, q_task_id)
                    continue
                # Check no in_progress task in that workflow
                wf_tasks = self.list_tasks(q_wf_id)
                if any(t["status"] == "in_progress" for t in wf_tasks):
                    continue
                # Start this task via helper
                started = self._start_task(q_task_id, q_wf_id, actual_model)
                if started is not None:
                    agent_store.dequeue_task(agent_name, q_task_id)
                break

        return {
            "task_id": task_id,
            "name": task["name"],
            "status": "done",
        }

    def escalate_task(self, workflow_id: str, task_id: str) -> dict | None:
        """Mark a task as escalated. Workflow becomes escalated too."""
        task = self._find_entry(task_id)
        if task is None:
            return None

        decision = validate_task_transition(task["status"], "escalated")
        if not decision.ok:
            return None

        self._update_entry_frontmatter(task_id, {"status": "escalated"})
        self._add_timeline_event(
            workflow_id,
            f"task '{task['name']}' escalated (agent: {task.get('assigned_agent') or 'unknown'})",
        )

        # Escalate workflow
        wf_decision = validate_workflow_transition("active", "escalated")
        if wf_decision.ok:
            self._update_entry_frontmatter(workflow_id, {"status": "escalated"})
            self._add_timeline_event(workflow_id, "workflow escalated")
        self._publish("task_updated", task_id)

        return {
            "task_id": task_id,
            "name": task["name"],
            "status": "escalated",
        }

    def block_task(self, workflow_id: str, task_id: str) -> dict | None:
        """Mark a task as blocked."""
        task = self._find_entry(task_id)
        if task is None:
            return None

        decision = validate_task_transition(task["status"], "blocked")
        if not decision.ok:
            return None

        self._update_entry_frontmatter(task_id, {"status": "blocked"})
        self._add_timeline_event(
            workflow_id,
            f"task '{task['name']}' blocked (agent: {task.get('assigned_agent') or 'unknown'})",
        )
        self._publish("task_updated", task_id)

        return {
            "task_id": task_id,
            "name": task["name"],
            "status": "blocked",
        }

    def assign_agent(self, task_id: str, agent: str | None) -> bool:
        """Assign an agent to a task."""
        task = self._find_entry(task_id)
        if task is None:
            return False
        self._update_entry_frontmatter(task_id, {"assigned_agent": agent})
        wf_id = None
        for tag in task.get("tags", []):
            if tag.startswith("wf-id:"):
                wf_id = tag.split(":", 1)[1]
                break
        if wf_id:
            agent_label = agent or "unassigned"
            self._add_timeline_event(wf_id, f"task '{task['name']}' assigned to {agent_label}")
            self._publish("task_updated", task_id)
        return True

    def insert_task(
        self,
        workflow_id: str,
        name: str,
        after_task_id: str,
        assigned_agent: str | None = None,
    ) -> dict | None:
        """Insert a new task into a workflow after a specific task.

        The new task is assigned an order value between after_task and the
        next task (after_idx + 1.5), so it sorts correctly without renumbering.

        Returns the new task dict, or None if after_task_id is not found.
        """
        tasks = self.list_tasks(workflow_id)

        after_idx = None
        for i, t in enumerate(tasks):
            if t["task_id"] == after_task_id:
                after_idx = i
                break

        if after_idx is None:
            return None

        # Order sits between after_task and the next task
        after_order = tasks[after_idx]["order"]
        if after_idx + 1 < len(tasks):
            next_order = tasks[after_idx + 1]["order"]
            new_order = (after_order + next_order) / 2
        else:
            new_order = after_order + 1

        now = _now_iso()
        date = _today()
        task_id = _generate_id("task")

        task_fm = {
            "record_id": task_id,
            "record_type": "workflow",
            "date": date,
            "created_at": now,
            "last_updated_at": now,
            "name": name,
            "status": "pending",
            "order": new_order,
            "assigned_agent": assigned_agent,
            "tags": ["wf-type:task", f"wf-id:{workflow_id}", "wf-status:pending"],
            "derived_from": workflow_id,
            "source_project": "_global",
            "tldr": name,
            "contributors": [],
        }

        self._write_entry(task_id, task_fm, f"Task: {name}", date)

        # Update the workflow's task_ids list
        wf = self._find_entry(workflow_id)
        if wf is not None:
            existing_ids = wf.get("task_ids") or []
            existing_ids.append(task_id)
            self._update_entry_frontmatter(workflow_id, {"task_ids": existing_ids})

        if assigned_agent:
            self._add_timeline_event(
                workflow_id,
                f"task '{name}' inserted (after {after_task_id}, assigned: {assigned_agent})",
            )
        else:
            self._add_timeline_event(
                workflow_id,
                f"task '{name}' inserted (after {after_task_id})",
            )

        self._publish("task_updated", task_id)

        return {
            "task_id": task_id,
            "name": name,
            "status": "pending",
            "order": new_order,
            "assigned_agent": assigned_agent,
            "workflow_id": workflow_id,
        }

    def cancel_workflow(self, workflow_id: str) -> bool:
        """Cancel an active workflow. Returns False if transition is invalid."""
        wf = self._find_entry(workflow_id)
        if wf is None:
            return False

        decision = validate_workflow_transition(wf["status"], "cancelled")
        if not decision.ok:
            return False

        self._update_entry_frontmatter(workflow_id, {"status": "cancelled"})
        self._add_timeline_event(workflow_id, "workflow cancelled")
        self._publish("workflow_updated", workflow_id)
        return True

    def get_workflow(self, workflow_id: str) -> dict | None:
        """Return full workflow info with tasks."""
        wf = self._find_entry(workflow_id)
        if wf is None:
            return None

        tasks = self.list_tasks(workflow_id)

        return {
            "workflow_id": workflow_id,
            "name": wf.get("name", ""),
            "status": wf["status"],
            "tasks": tasks,
            "created_at": wf.get("created_at", ""),
        }

    def get_timeline(self, workflow_id: str) -> list[dict]:
        """Return chronological events from the workflow body."""
        wf = self._find_entry(workflow_id)
        if wf is None:
            return []

        body = wf.get("_body", "")
        events = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("- [") and "] " in line:
                # Parse: - [timestamp] event
                bracket_end = line.index("] ", 2)
                timestamp = line[3:bracket_end]
                event_text = line[bracket_end + 2:]
                events.append({
                    "timestamp": timestamp,
                    "event": event_text,
                })

        return events
