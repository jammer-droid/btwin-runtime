---
name: bt:status
description: Use when the agent needs to check task progress or details of a specific workflow
---

# Workflow Status

## Overview
Retrieves detailed status of a specific workflow, including all tasks and their current states.

## API Call
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s "$BTWIN_API_URL/api/workflows/<workflow_id>"
```

## Parameters
- `<workflow_id>` (required): Workflow ID (e.g., `wf-xxxxxx`)

## Response
**Success (200):** Returns workflow detail with `tasks[]`, each containing `task_id`, `name`, `status`, `order`. Possible task statuses: `done`, `in_progress`, `pending`, `blocked`, `escalated`.

**Failure (404):** Workflow not found. Verify the ID.

## Notes
- If `workflow_id` is unknown, use `bt:list` first to find it.
- `BTWIN_API_URL` defaults to the local B-TWIN API server. Override it if your runtime uses a different endpoint.
