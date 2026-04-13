---
name: bt:next
description: Use when an agent has finished its current task and needs to dequeue and start the next one
---

# Next Task

## Overview
Dequeues the next task from the agent's queue and transitions it to `in_progress`. Use after completing a task to continue with queued work.

## API Call
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s -X POST "$BTWIN_API_URL/api/agents/<agent-name>/next-task" \
  -H "Content-Type: application/json" \
  -d '{"actual_model": "<optional-model>"}'
```

Body can be empty `{}`.

## Parameters
- `<agent-name>` (required): Agent name in URL path
- `actual_model` (optional): Model name for tracking

## Response
**Success (200):** Returns the started task info (dequeued and set to `in_progress`).

**No task available (404):** `{error: "NO_VIABLE_TASK"}`. Queue is empty or next task is blocked. Use `bt:queue` to add tasks or `bt:list` to check workflows.

## Notes
- Agent must be registered first (use `bt:register`).
- Sequential execution enforced: prior tasks in the workflow must be `done`.
- `BTWIN_API_URL` defaults to the local B-TWIN API server. Override it if your runtime uses a different endpoint.
