---
name: bt:create
description: Use when a multi-step plan needs to be tracked as a sequential workflow with ordered tasks
---

# Create Workflow

## Overview
Creates a new workflow with an ordered list of tasks. Tasks execute sequentially — each must complete before the next can start.

## API Call
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s -X POST "$BTWIN_API_URL/api/workflows" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "<workflow-name>",
    "tasks": ["<task1>", "<task2>", "<task3>"],
    "assigned_agents": ["<agent1>", null, "<agent3>"]
  }'
```

## Parameters
- `name` (required): Descriptive workflow name (e.g., "API endpoint refactoring")
- `tasks` (required): Ordered array of task names. Use action-oriented names (e.g., "Implement JWT auth middleware")
- `assigned_agents` (optional): Array of agent names per task. Use `null` for unassigned.

## Response
**Success (201):** Returns `workflow_id`, `name`, `status: "active"`, and `tasks[]` with `task_id`, `name`, `status: "pending"`, `order`.

**Failure (422):** Empty `name` or `tasks`.

## Notes
- All tasks start as `pending`. The workflow starts as `active`.
- Sequential execution: previous task must be `done` before the next can begin.
- `BTWIN_API_URL` defaults to the local B-TWIN API server. Override it if your runtime uses a different endpoint.
