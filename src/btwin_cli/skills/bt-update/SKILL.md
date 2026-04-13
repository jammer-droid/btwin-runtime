---
name: bt:update
description: Use when a task status needs to change (start, complete, block, escalate) or an agent needs assignment to a task
---

# Update Task

## Overview
Changes task status or assigns an agent to a task. Supports two operations: status transition and agent assignment.

## API Call

### Status Change
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s -X PATCH "$BTWIN_API_URL/api/workflows/<workflow_id>/tasks/<task_id>/status" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "<new_status>",
    "actual_model": "<optional-model>"
  }'
```

**Parameters:**
- `status` (required): One of `in_progress`, `done`, `blocked`, `escalated`
- `actual_model` (optional): Model name for tracking purposes

### Agent Assignment
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s -X PATCH "$BTWIN_API_URL/api/workflows/<workflow_id>/tasks/<task_id>/agent" \
  -H "Content-Type: application/json" \
  -d '{"agent": "<agent-name>"}'
```

**Parameters:**
- `agent` (required): Agent name, or `null` to unassign

## Response
**Success (200):** Returns updated task info.

**Failure:** 404 (workflow/task not found), 409 (invalid status transition).

## Notes
**Valid status transitions:**
- `pending` -> `in_progress` (requires all prior tasks `done`)
- `in_progress` -> `done` | `blocked` | `escalated`
- `blocked` -> `in_progress`

Only one task can be `in_progress` at a time. Workflow auto-completes when all tasks are `done`.
- `BTWIN_API_URL` defaults to the local B-TWIN API server. Override it if your runtime uses a different endpoint.
