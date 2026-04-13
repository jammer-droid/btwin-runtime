"""Conductor Loop — automated task dispatch on completion."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from btwin_core.resource_paths import resolve_bundled_providers_path

if TYPE_CHECKING:
    from btwin_core.agent_store import AgentStore
    from btwin_core.terminal_manager import TerminalManager
    from btwin_core.workflow_engine import WorkflowEngine

log = logging.getLogger(__name__)


class ConductorLoop:
    async def on_task_completed(
        self,
        workflow_id: str,
        task_id: str,
        workflow_engine: "WorkflowEngine",
        agent_store: "AgentStore",
        terminal_manager: "TerminalManager",
        storage_data_dir=None,
    ) -> bool:
        """React to task completion. Returns True if next task was dispatched."""
        tasks = workflow_engine.list_tasks(workflow_id)

        # Find next pending task
        next_task = None
        for t in tasks:
            if t["status"] == "pending":
                next_task = t
                break

        if next_task is None:
            log.info("Conductor: all tasks done in workflow %s", workflow_id)
            return False

        next_agent = next_task.get("assigned_agent")
        if not next_agent:
            log.info("Conductor: next task %s has no assigned agent, skipping", next_task["task_id"])
            return False

        # Send to Conductor Agent first (if exists)
        completed_task = next((t for t in tasks if t["task_id"] == task_id), None)
        await self._notify_conductor_agent(
            completed_task, next_task, workflow_id,
            agent_store, terminal_manager,
        )

        # Start next task in workflow engine
        workflow_engine.start_next_task(workflow_id)

        # Load attached guides
        guide_content = None
        attached = next_task.get("attached_guides", [])
        if attached:
            from btwin_core.guide_loader import GuideLoader
            data_dir = agent_store._data_dir if hasattr(agent_store, "_data_dir") else Path.home() / ".btwin"
            guide_loader = GuideLoader(data_dir)
            guide_content = guide_loader.get_guides_content(attached) or None

        # Dispatch to agent terminal
        await self._dispatch_to_terminal(
            next_agent, next_task, workflow_id,
            agent_store, terminal_manager,
            guide_content=guide_content,
            storage_data_dir=storage_data_dir,
        )

        return True

    async def _notify_conductor_agent(
        self, completed_task, next_task, workflow_id,
        agent_store, terminal_manager,
    ):
        """Send result to Conductor Agent for instruction generation."""
        agents = agent_store.list_agents()
        conductor = None
        for a in agents:
            if "conductor" in a.get("capabilities", []):
                conductor = a
                break

        if conductor is None:
            log.debug("Conductor: no conductor agent registered, using simple mode")
            return

        session = self._find_session(terminal_manager, conductor["name"])
        if session is None:
            log.debug("Conductor: conductor agent has no active terminal")
            return

        completed_name = completed_task["name"] if completed_task else "unknown"
        completed_agent = completed_task.get("assigned_agent", "unknown") if completed_task else "unknown"

        prompt = (
            f"[CONDUCTOR] Task completed in workflow {workflow_id}:\n"
            f"- Completed: {completed_name} (agent: {completed_agent})\n"
            f"- Next task: {next_task['name']} (agent: {next_task.get('assigned_agent', 'unknown')})\n"
            f"\nAnalyze the completed task's results and decide the next action.\n"
            f"\n## If the completed task is a review:\n"
            f"Determine if the review passes or needs fixes.\n"
            f"\nTo approve (proceed to next task):\n"
            f"```\n"
            f"curl -s -X POST http://localhost:8787/api/conductor/decision \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -d '{{\"workflow_id\": \"{workflow_id}\", \"task_id\": \"{next_task['task_id']}\", \"decision\": \"approve\", \"summary\": \"YOUR_SUMMARY\"}}'\n"
            f"```\n"
            f"\nTo request fixes:\n"
            f"```\n"
            f"curl -s -X POST http://localhost:8787/api/conductor/decision \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -d '{{\"workflow_id\": \"{workflow_id}\", \"task_id\": \"{next_task['task_id']}\", \"decision\": \"request_fix\", \"summary\": \"YOUR_SUMMARY\", \"feedback\": \"DETAILED_FEEDBACK\"}}'\n"
            f"```\n"
            f"\n## Otherwise:\n"
            f"The next task will be automatically dispatched.\n"
        )
        await session.adapter.write(prompt.encode("utf-8"))

    async def request_agent_assignment(
        self,
        workflow_id: str,
        tasks: list[dict],
        agents: list[dict],
        terminal_manager: "TerminalManager",
        agent_store: "AgentStore",
    ):
        """Ask Conductor Agent to assign agents to tasks."""
        conductor = None
        for a in agent_store.list_agents():
            if "conductor" in a.get("capabilities", []):
                conductor = a
                break

        if conductor is None:
            log.debug("Conductor: no conductor agent registered, cannot request assignment")
            return

        session = self._find_session(terminal_manager, conductor["name"])
        if session is None:
            log.debug("Conductor: conductor agent has no active terminal")
            return

        task_lines = []
        for i, t in enumerate(tasks, 1):
            task_lines.append(f"{i}. {t['name']} (role: {t.get('role', 'unknown')}, task_id: {t['task_id']})")

        agent_lines = []
        for a in agents:
            caps = ", ".join(a.get("capabilities", []))
            agent_lines.append(
                f"- {a['name']}: model={a.get('model', '?')}, capabilities=[{caps}], queue_length={a.get('queue_length', 0)}"
            )

        prompt = (
            f"[CONDUCTOR] New workflow created: {workflow_id}\n"
            f"\nTasks:\n" + "\n".join(task_lines) + "\n"
            f"\nRegistered agents:\n" + "\n".join(agent_lines) + "\n"
            f"\nAssign the best agent to each task.\n"
            f"\nUse this curl command:\n"
            f"```\n"
            f"curl -s -X POST http://localhost:8787/api/conductor/assign \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -d '{{\"workflow_id\": \"{workflow_id}\", \"assignments\": [{{\"task_id\": \"TASK_ID\", \"agent\": \"AGENT_NAME\", \"reason\": \"WHY\"}}]}}'\n"
            f"```\n"
        )
        await session.adapter.write(prompt.encode("utf-8"))

    async def _dispatch_to_terminal(
        self, agent_name, task, workflow_id,
        agent_store, terminal_manager,
        prompt_guide: str | None = None,
        guide_content: str | None = None,
        storage_data_dir=None,
    ):
        """Write task info to agent's terminal."""
        session = self._find_session(terminal_manager, agent_name)

        if session is None and storage_data_dir is not None:
            # Auto-spawn terminal using provider config
            session = await self._spawn_terminal_from_config(
                agent_name, agent_store, terminal_manager, storage_data_dir
            )

        if session is None:
            log.warning("Conductor: could not dispatch to agent %s (no terminal, no config)", agent_name)
            return

        parts = []
        parts.append(f"You have been assigned a task in workflow {workflow_id}:")
        parts.append(f"Task: {task['name']}")
        parts.append(f"Task ID: {task['task_id']}")

        # Include task description if available
        task_desc = task.get("description") or task.get("tldr")
        if task_desc:
            parts.append(f"Description: {task_desc}")

        # Template prompt guide (with variable substitution)
        if prompt_guide:
            expanded = prompt_guide.replace("{workflow_id}", workflow_id).replace("{task_id}", task["task_id"])
            parts.append(f"\n{expanded}")

        # Attached guides
        if guide_content:
            parts.append(f"\n{guide_content}")

        # Always add completion instruction if no prompt_guide (prompt_guide includes it)
        if not prompt_guide:
            parts.append(f"\nWhen done, report completion with: /bt:update {workflow_id} {task['task_id']} status=done")

        prompt = "\n".join(parts)
        # Send prompt text followed by carriage return to submit to CLI
        await session.adapter.write(prompt.encode("utf-8"))
        await session.adapter.write(b"\r")

    async def _spawn_terminal_from_config(self, agent_name, agent_store, terminal_manager, data_dir):
        """Spawn a terminal for agent using provider config."""
        import json
        from pathlib import Path

        agent = agent_store.get_agent(agent_name)
        if agent is None:
            return None

        model_id = agent.get("model", "")

        # Load providers config
        config_path = Path(data_dir) / "providers.json"
        if not config_path.exists():
            bundled = resolve_bundled_providers_path()
            if bundled is not None:
                config_path = bundled

        command = None
        args = []
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            for provider in config.get("providers", []):
                for model in provider.get("models", []):
                    if model["id"] == model_id:
                        command = provider["cli"]
                        args = list(provider.get("default_args", []))
                        # Reasoning level
                        reasoning_level = agent.get("reasoning_level")
                        reasoning_arg = provider.get("reasoning_arg")
                        if reasoning_level and reasoning_arg:
                            expanded = reasoning_arg.replace("{level}", reasoning_level)
                            args.extend(expanded.split())
                        # Bypass permissions
                        if agent.get("bypass_permissions", False):
                            if provider["cli"] == "claude":
                                args.append("--dangerously-skip-permissions")
                            elif provider["cli"] == "codex":
                                args.append("--full-auto")
                        break
                if command:
                    break

        if command is None:
            log.warning("Conductor: no CLI configured for agent %s (model: %s)", agent_name, model_id)
            return None

        try:
            session = await terminal_manager.create_session(agent_name, command, args)
            log.info("Conductor: spawned terminal for agent %s (%s)", agent_name, command)
            return session
        except Exception:
            log.exception("Conductor: failed to spawn terminal for %s", agent_name)
            return None

    async def dispatch_first_task(
        self,
        workflow_id: str,
        workflow_engine: "WorkflowEngine",
        agent_store: "AgentStore",
        terminal_manager: "TerminalManager",
        storage_data_dir=None,
    ) -> bool:
        """Dispatch the first in-progress task in a workflow (initial start).

        Called after workflow creation to auto-dispatch the first task to its
        assigned agent terminal.  Returns True if dispatch succeeded.
        """
        tasks = workflow_engine.list_tasks(workflow_id)

        first_in_progress = None
        for t in tasks:
            if t["status"] == "in_progress":
                first_in_progress = t
                break

        if first_in_progress is None:
            log.debug("Conductor: no in_progress task found for initial dispatch in %s", workflow_id)
            return False

        agent_name = first_in_progress.get("assigned_agent")
        if not agent_name:
            log.debug(
                "Conductor: first task %s has no assigned agent, skipping dispatch",
                first_in_progress["task_id"],
            )
            return False

        await self._dispatch_to_terminal(
            agent_name, first_in_progress, workflow_id,
            agent_store, terminal_manager,
            storage_data_dir=storage_data_dir,
        )
        return True

    def _find_session(self, terminal_manager, agent_name):
        """Find a running terminal session for agent."""
        for session in terminal_manager.list_sessions():
            if session.agent_name == agent_name and session.status == "running":
                return session
        return None
