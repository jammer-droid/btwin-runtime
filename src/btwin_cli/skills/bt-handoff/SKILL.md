---
name: bt:handoff
description: Use when the user wants to leave a structured handoff for the next worker, especially across sessions, branches, or unfinished implementation phases
---

# Save Handoff to B-TWIN

## Overview

Use this skill when the goal is not just to save progress, but to leave a narrative handoff that helps the next worker resume in a fresh session without losing context.

Default choice:
- `btwin_convo_record`

Use `btwin_end_session` instead only when the current work is clearly being wrapped up as a session close.

## Preconditions

1. Read `btwin_get_guidelines` if recording rules are not already loaded.
2. Use `/bt:register` only if the session also needs workflow/orchestration operations.
   - Registration does not automatically become contributor attribution on every btwin memory write path.

## When to Use

Use `bt:handoff` when:

- the current worker is about to stop and another worker will continue later
- the user explicitly asks for a handoff note
- a branch or prototype has meaningful narrative context that would be lost in a raw diff
- the next worker needs to understand why the work started, what has already been decided, and what remains

Do **not** use this for ordinary “save progress” requests with no handoff intent. Use `bt:save` for those.

## Tool Selection

| Situation | Tool |
|---|---|
| Standard handoff note for continuing work | `btwin_convo_record` |
| End-of-session handoff with explicit closeout | `btwin_end_session` |
| Standalone note with no session/history emphasis | `btwin_record` |

Rule of thumb:
- **If unsure, use `btwin_convo_record`.**

## Handoff Structure

A handoff record should preserve narrative context, not just a changelog.

Prefer these sections:

1. **Background**
   - Why this work started
   - Which bug, request, review, or plan triggered it
   - Any important upstream context

2. **Intent and Decisions**
   - What direction was chosen
   - Which alternatives were rejected
   - Important assumptions or constraints

3. **Current State**
   - What is done
   - What is in progress
   - What is intentionally out of scope

4. **Verification**
   - Tests, builds, searches, reviews, or manual checks completed
   - Anything that was not verified yet

5. **Risks and Open Questions**
   - Flaky areas
   - Provider/tooling limits
   - Decisions still pending

6. **Next Steps**
   - Concrete follow-up tasks in recommended order
   - What the next worker should do first

7. **Starter Context**
   - branch name
   - latest relevant commit
   - key files
   - key docs/plans/reports
   - commands that help the next worker restart quickly

Not every section is mandatory, but handoffs should strongly bias toward this structure.

## Metadata Rules

### `tldr`
- 1-3 sentences, max 200 characters
- include the work topic plus the handoff outcome
- mention the next worker’s continuation point when possible

Good examples:
- `Persistent process prototype docs were added and committed. Next worker should execute the prototype plan from Task 1 under src/btwin/prototypes/persistent_sessions/.`
- `Optimistic thread UX branch merged to main after test hardening. Next work is manual verification, then Persistent Process Phase A.`

### `tags`
- 3-6 tags
- include project, work type, and handoff topic
- example: `btwin-service`, `handoff`, `docs`, `prototype`, `runtime`

### `subject_projects`
- include only directly related projects

## Workflow

1. Decide whether the user wants a real handoff or a normal save
2. Choose the tool using the table above
3. Write a concise but narrative handoff summary using the handoff structure
4. Add `tldr`, `tags`, and `subject_projects`
5. Call the selected btwin MCP tool
6. Generate a **dispatch sentence** — a single copy-pasteable instruction for the next worker
7. **Write `HANDOFF.md`** to the project root using the CLI helper below
8. Report the saved result with the dispatch sentence

## Local Snapshot + Global Archive

After saving to btwin, run the CLI helper to write the latest snapshot to `HANDOFF.md` in the project root directory and append the archive row to the global B-TWIN project archive (by default `~/.btwin/projects/<project_key>/handoffs.jsonl`). This gives the next worker a fast restart surface without keeping project-local archive history in the working tree.

1. The next worker can see what to do without searching btwin
2. B-TWIN keeps an append-only global archive of who did what for that project identity

### File Format

`HANDOFF.md` is the latest handoff snapshot only. The historical archive lives in the global B-TWIN project archive, which defaults to `~/.btwin/projects/<project_key>/handoffs.jsonl`.

```markdown
# Current Handoff

- **Updated**: 2026-04-11
- **Record**: convo-...
- **Summary**: one-line summary
- **Dispatch**: dispatch sentence
```

### Rules

- **Overwrite** `HANDOFF.md` with the latest snapshot only
- Append each archive row to the global handoff archive
- If `HANDOFF.md` is not already tracked by git, ensure the project’s `.gitignore` contains `HANDOFF.md` exactly once
- If `HANDOFF.md` is already tracked by git, do not modify `.gitignore`; just update the snapshot

### CLI Helper

Use the CLI to write the local snapshot and global archive:

```bash
btwin handoff --record-id convo-123 --summary "Short handoff summary" --dispatch "Next worker instruction" --tag btwin-service --tag handoff
```

Add `--background`, `--intent`, `--current-state`, `--verification`, `--risks`, `--next-steps`, and `--starter-context` when you have those details available.

## Rules

- Prefer absolute file paths
- Prefer absolute dates over relative dates
- Do not write a raw transcript dump
- Do not invent progress, verification, or decisions that did not happen
- Optimize for the next worker’s restart speed, not for archival completeness

## Dispatch Sentence

After saving the handoff, generate a **dispatch sentence**: a single instruction the user can copy-paste into a new session to resume work.

### Rules

- Exactly one sentence
- Must be self-contained — the next worker should be able to start with only this sentence
- Include: what to do, where to find context (record_id or key file), and which branch/commit if relevant
- Do not assume the next worker has any prior context
- Write in the same language the user has been using in the conversation

### Examples

- `btwin record convo-140832123456789에 핸드오프가 저장되어 있으니 읽고, main 브랜치 dd9a6c1 커밋 기준으로 src/btwin/skills/bt-handoff/SKILL.md에 dispatch sentence 기능을 구현해줘.`
- `Read handoff at btwin record convo-140832123456789, then continue persistent process prototype Phase A starting from Task 1 in docs/plans/persistent-process-plan.md on branch feat/persistent-process.`

## Result Format

After saving, report:
- which tool was used
- one-line summary of what was handed off
- record id or saved path when available
- **dispatch sentence** — highlighted in a code block for easy copy

Example:

```text
Saved with btwin_convo_record.
- Summary: persistent process prototype design and plan were completed; next worker should start Task 1 of the prototype plan
- record_id: convo-123456789012345
```

**Next worker dispatch:**
```
btwin record convo-123456789012345에 핸드오프가 저장되어 있으니 읽고, feat/persistent-process 브랜치에서 docs/plans/persistent-process-plan.md의 Task 1부터 프로토타입 구현을 시작해줘.
```
