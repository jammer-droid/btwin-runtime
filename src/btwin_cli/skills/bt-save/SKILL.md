---
name: bt:save
description: Use when the user asks to save the current conversation, progress update, or key decisions into btwin memory
---

# Save to B-TWIN

## Overview

Use this skill to store the current conversation or a concise progress summary in B-TWIN with the right MCP memory tool.

Default choice:
- `btwin_convo_record`

Use another tool only when the session structure clearly requires it.

If the goal is to leave a narrative restart note for the next worker rather than a normal progress save, use `bt:handoff` instead.

## Preconditions

1. Read `btwin_get_guidelines` if recording rules are not already loaded.
2. Use `/bt:register` only if the session will also use workflow/orchestration Skills.
   - Current save flows do **not** automatically convert agent registration into contributor attribution on every MCP/proxy path.

## Tool Selection

| Situation | Tool |
|---|---|
| One save for the current conversation or progress summary | `btwin_convo_record` |
| Long-running session already managed as an open topic | `btwin_end_session` |
| Start a tracked session before a longer work period | `btwin_start_session` |
| Save a standalone note rather than conversation memory | `btwin_record` |

Rule of thumb:
- **If unsure, use `btwin_convo_record`.**

## What to Record

Include only content that actually appeared in the conversation.
Prefer a compressed structured summary over raw transcript dumps.

Useful sections, in priority order:
1. **Background** — why this work happened
2. **Decisions** — what was chosen and why
3. **Implementation** — files, APIs, config, structure changed
4. **Verification** — tests, searches, commands, checks run
5. **Next steps** — follow-up tasks or open questions
6. **Issues** — bugs, risks, blockers discovered

Not every section is required.
Do not invent missing details.

## Metadata Rules

### `tldr`
- 1-3 sentences, max 200 characters
- include searchable keywords
- state the result, decision, or issue concretely

Good examples:
- `OMX project scope hid the global btwin MCP config. Added repo-local btwin mcp-proxy config and verified the tools loaded.`
- `Confirmed ChromaDB cross-process visibility bug. API and direct MCP server use separate PersistentClient instances, so search results diverge.`

### `tags`
- 3-5 tags
- prefer: project, work type, core topic
- example: `btwin-service`, `docs`, `mcp`, `orchestration`, `bug`

### `subject_projects`
- include only directly related projects

## Workflow

1. Decide whether the user wants a conversation save, session close, or standalone note
2. Choose the tool using the table above
3. Write a concise structured summary
4. Write `tldr`, `tags`, and `subject_projects`
5. Call the selected btwin MCP tool
6. Report the saved result briefly

## Rules

- Prefer absolute paths when recording files
- Prefer absolute dates over relative dates
- Do not record unverified claims as facts
- Do not over-save sensitive or irrelevant transcript content
- Prefer structured summaries to long raw conversation copies

## Result Format

After saving, report:
- which tool was used
- one-line summary of what was saved
- record id or saved path when available

Example:

```text
Saved with btwin_convo_record.
- Summary: root cause and fix for missing btwin MCP config in OMX project scope
- record_id: convo-123456789012345
```
