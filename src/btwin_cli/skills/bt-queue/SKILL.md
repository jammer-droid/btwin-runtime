---
name: bt:queue
description: Use when a specific task needs to be assigned to an agent's work queue for later execution
---

# Queue Task

## Overview
Adds a task to an agent's queue. Queued tasks are executed in order via `bt:next`.

## API Call
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s -X POST "$BTWIN_API_URL/api/agents/<agent-name>/queue" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "<wf-xxx>",
    "task_id": "<task-yyy>"
  }'
```

## Parameters
- `<agent-name>` (required): Agent name in URL path
- `workflow_id` (required): Workflow ID
- `task_id` (required): Task ID to enqueue

## Response
**Success (200):** Returns `{queue: [...]}` with the current queue state.

**Failure:**
- 404 `TASK_NOT_FOUND`: Invalid task ID. Use `bt:status` to verify.
- 404 `AGENT_NOT_FOUND`: Agent not registered. Use `bt:register` first.

## Notes
- Agent must be registered first.
- Duplicate tasks are rejected.
- Use `bt:next` to start execution after queuing.
- If IDs are unknown, use `bt:list` and `bt:status` to look them up.
- `BTWIN_API_URL` defaults to the local B-TWIN API server. Override it if your runtime uses a different endpoint.
