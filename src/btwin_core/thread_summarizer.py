"""Generate thread summaries using LLM from collected messages and contributions."""

from __future__ import annotations

from btwin_core.thread_store import ThreadStore


THREAD_SUMMARY_PROMPT = """Summarize this thread discussion into a structured summary.
The thread topic is: {topic}
The protocol used is: {protocol}

Include:
- Key points discussed
- Agreements reached
- Decision (if any)
- Open questions or next steps

Write in the same language as the discussion."""


class ThreadSummarizer:
    def __init__(self, thread_store: ThreadStore, llm_client: object) -> None:
        self._threads = thread_store
        self._llm = llm_client

    def generate(self, thread_id: str) -> dict | None:
        meta = self._threads.get_thread(thread_id)
        if meta is None:
            return None

        messages = self._threads.list_messages(thread_id)
        contributions = self._threads.list_contributions(thread_id)

        all_items = messages + contributions
        all_items.sort(key=lambda item: item.get("created_at", ""))

        formatted = "\n\n".join(
            f"**{item.get('from', item.get('agent', 'unknown'))}** "
            f"({item.get('msg_type', 'contribution')}):\n{item.get('_content', '')}"
            for item in all_items
        )

        summary = self._llm.summarize_thread(
            content=formatted,
            topic=meta["topic"],
            protocol=meta["protocol"],
        )
        decision = self._extract_decision(contributions)

        return {"summary": summary, "decision": decision}

    @staticmethod
    def _extract_decision(contributions: list[dict]) -> str | None:
        """Extract decision text from the last contribution that has a ## decision section."""
        import re

        for contribution in reversed(contributions):
            content = contribution.get("_content", "")
            match = re.search(
                r"^##\s+decision\s*\n(.*?)(?=\n##\s|\Z)",
                content,
                re.MULTILINE | re.IGNORECASE | re.DOTALL,
            )
            if match:
                return match.group(1).strip()
        return None
