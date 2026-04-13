"""Format thread context and messages as stdin-injectable text for agent CLI processes."""

from __future__ import annotations


def _participant_name(p: dict | str) -> str:
    """Extract participant name from either a dict with 'name' key or a plain string."""
    if isinstance(p, dict):
        return p.get("name", str(p))
    return str(p)


class ContextFormatter:
    @staticmethod
    def _message_visible_to_agent(msg: dict, agent_name: str | None) -> bool:
        """Return whether a stored message should appear in an agent snapshot."""
        delivery_mode = str(msg.get("delivery_mode", "auto") or "auto")
        if delivery_mode != "direct":
            return True
        if not agent_name:
            return True

        sender = str(msg.get("from", ""))
        if sender == agent_name:
            return True

        target_agents = msg.get("target_agents") or []
        return agent_name in target_agents

    @staticmethod
    def build_thread_snapshot(
        thread: dict,
        messages: list[dict],
        contributions: list[dict],
        agent_name: str | None = None,
    ) -> dict:
        """Build a compact, btwin-owned snapshot for prompt rendering."""
        recent_messages = []
        visible_messages = [
            msg for msg in messages
            if ContextFormatter._message_visible_to_agent(msg, agent_name)
        ]
        for msg in visible_messages[-8:]:
            recent_messages.append({
                "from": msg.get("from", "unknown"),
                "content": msg.get("_content", msg.get("content", "")),
            })

        recent_contributions = []
        for contrib in contributions[-4:]:
            recent_contributions.append({
                "agent": contrib.get("agent", "unknown"),
                "phase": contrib.get("phase", ""),
                "content": contrib.get("_content", contrib.get("content", "")),
            })

        return {
            "thread_id": thread["thread_id"],
            "topic": thread["topic"],
            "participants": [_participant_name(p) for p in thread.get("participants", [])],
            "current_phase": thread.get("current_phase", "none"),
            "interaction_mode": thread.get("interaction_mode", "discuss"),
            "recent_messages": recent_messages,
            "recent_contributions": recent_contributions,
            "shared_artifacts": thread.get("shared_artifacts", []),
            "pending_requests": thread.get("pending_requests", []),
            "agent_specific_summary": thread.get("agent_specific_summary", ""),
            "agent_name": agent_name,
        }

    @staticmethod
    def render_oneshot_prompt(snapshot: dict, ask: str) -> str:
        """Render a bounded prompt from a thread snapshot and current ask."""
        parts = [
            f"## Thread: {snapshot['topic']}",
            f"Thread ID: {snapshot['thread_id']}",
            f"Current phase: {snapshot.get('current_phase', 'none')}",
            f"Interaction mode: {snapshot.get('interaction_mode', 'discuss')}",
            f"Participants: {', '.join(snapshot.get('participants', []))}",
        ]

        if snapshot.get("agent_name"):
            parts.extend([
                "",
                "## Your Identity",
                f'You are "{snapshot["agent_name"]}".',
            ])

        recent_messages = snapshot.get("recent_messages", [])
        if recent_messages:
            parts.extend(["", "## Recent Messages"])
            for msg in recent_messages:
                parts.append(f"  {msg['from']}: {msg['content']}")

        recent_contributions = snapshot.get("recent_contributions", [])
        if recent_contributions:
            parts.extend(["", "## Recent Contributions"])
            for contrib in recent_contributions:
                parts.append(f"  {contrib['agent']} ({contrib['phase']}):")
                content = contrib.get("content", "")
                for line in str(content).split("\n")[:10]:
                    parts.append(f"    {line}")

        if snapshot.get("agent_specific_summary"):
            parts.extend([
                "",
                "## Agent Summary",
                str(snapshot["agent_specific_summary"]),
            ])

        parts.extend([
            "",
            "Current ask:",
            ask,
        ])

        return "\n".join(parts)

    @staticmethod
    def render_routing_snapshot(snapshot: dict, current_message: str) -> str:
        """Render a compact routing-only snapshot for the mini router judge."""
        parts = [
            f"Thread topic: {snapshot.get('topic', '')}",
            f"Current phase: {snapshot.get('current_phase', 'none')}",
            f"Interaction mode: {snapshot.get('interaction_mode', 'discuss')}",
            f"Participants: {', '.join(snapshot.get('participants', []))}",
        ]

        recent_messages = snapshot.get("recent_messages", [])
        if recent_messages:
            parts.append("Recent messages:")
            for msg in recent_messages[-4:]:
                parts.append(f"- {msg.get('from', 'unknown')}: {msg.get('content', '')}")

        parts.append(f"Current human message: {current_message}")
        return "\n".join(parts)

    @staticmethod
    def format_initial_context(
        thread: dict,
        protocol: dict,
        messages: list[dict],
        contributions: list[dict],
        agent_name: str | None = None,
    ) -> str:
        parts = []

        # Identity block — only when agent_name is provided
        if agent_name:
            parts.append("## Your Identity")
            parts.append(f'You are "{agent_name}".')
            parts.append("")
            parts.append("## Thread History Convention")
            parts.append(f'- Messages labeled "{agent_name}" are YOUR previous messages — you wrote them.')
            parts.append("- Messages from other participants are NOT yours.")
            parts.append("- When you see your own previous messages, treat them as context for continuity.")
            parts.append("")
            parts.append("## Spawn Bootstrap")
            parts.append("The thread already exists and you have already been invited or joined it.")
            parts.append("Do NOT ask whether the thread should be created or who should create it.")
            parts.append("Respond with a short acknowledgement, readiness note, or initial stance.")
            parts.append("Keep the first reply brief and direct.")
            parts.append("")

        # Thread summary
        participant_names = ", ".join(
            _participant_name(p) for p in thread.get("participants", [])
        )
        parts.append(f"## Thread Title: {thread['topic']}")
        if thread.get("thread_id"):
            parts.append(f"Thread ID: {thread['thread_id']}")
            parts.append("Use the Thread ID above for tool calls and API identifiers.")
            parts.append("Do not use the thread title as a thread_id.")
        parts.append(f"Protocol: {protocol['name']} — {protocol.get('description', '')}")
        parts.append(f"Current phase: {thread.get('current_phase', 'none')}")
        parts.append(f"Participants: {participant_names}")

        # Phase definitions
        parts.append("")
        parts.append("## Protocol Phases")
        for phase in protocol.get("phases", []):
            actions = ", ".join(phase.get("actions", []))
            parts.append(f"  - {phase['name']}: {phase.get('description', '')} [{actions}]")
            if phase.get("template"):
                for section in phase["template"]:
                    req = " (required)" if section.get("required") else ""
                    parts.append(f"    - {section['section']}{req}: {section.get('guidance', '')}")

        # Previous messages
        if messages:
            parts.append("")
            parts.append("## Previous Messages")
            for msg in messages:
                sender = msg.get("from", "unknown")
                parts.append(f"  {sender}: {msg.get('_content', '')}")

        # Previous contributions
        if contributions:
            parts.append("")
            parts.append("## Previous Contributions")
            for c in contributions:
                parts.append(f"  {c.get('agent', 'unknown')} ({c.get('phase', '')}):")
                content = c.get("_content", "")
                for line in content.split("\n")[:10]:
                    parts.append(f"    {line}")
                if content.count("\n") > 10:
                    parts.append("    ...")

        # Instructions
        parts.append("")
        parts.append("## Instructions")
        parts.append("Acknowledge your participation and state your initial position briefly.")
        parts.append("Do NOT perform extensive research, read files, or run tools at this stage.")
        parts.append("Detailed work will be requested in subsequent messages.")
        parts.append("Use btwin_thread_contribute for structured contributions when the protocol phase requires it.")
        parts.append("Regular messages are captured automatically.")

        # Language directive — only when agent_name is provided
        if agent_name:
            parts.append("")
            parts.append("## Response Language")
            parts.append("IMPORTANT: Respond in the same language as the most recent human message in this thread.")
            parts.append("Protocol instructions are in English for standardization — this does NOT mean respond in English.")
            parts.append("If no human message exists yet, respond in the language of the thread topic.")

        return "\n".join(parts)

    @staticmethod
    def format_message_relay(
        from_agent: str,
        content: str,
        thread_id: str,
        phase_name: str | None = None,
    ) -> str:
        header = f"[Thread: {thread_id}"
        if phase_name:
            header += f" | Phase: {phase_name}"
        header += "]"

        return f"{header}\n\n{from_agent} said:\n{content}"

    @staticmethod
    def format_phase_transition(
        old_phase: str,
        new_phase_def: dict,
    ) -> str:
        parts = [
            f"[Phase changed: {old_phase} → {new_phase_def['name']}]",
            "",
            f"New phase: {new_phase_def['name']}",
            f"Description: {new_phase_def.get('description', '')}",
            f"Actions: {', '.join(new_phase_def.get('actions', []))}",
        ]

        if new_phase_def.get("template"):
            parts.append("Template sections:")
            for section in new_phase_def["template"]:
                req = " (required)" if section.get("required") else ""
                parts.append(f"  - {section['section']}{req}: {section.get('guidance', '')}")

        return "\n".join(parts)
