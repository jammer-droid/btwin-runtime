---
name: bt:list
description: Use when the agent needs to see all workflows or find a specific workflow ID
---

# List Workflows

## Overview
Retrieves all workflows. Use this to get an overview of workflow statuses or to find a workflow ID for bt:status.

## API Call
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s "$BTWIN_API_URL/api/workflows"
```

No parameters.

## Response
**Success (200):** Returns `{items: [...]}` with each item containing `workflow_id`, `name`, `status`, `created_at`.

Group results by status when presenting: `active`, `completed`, `escalated`, `cancelled`.

## Notes
- Returns all workflows unfiltered. Group by status for clarity.
- `BTWIN_API_URL` defaults to the local B-TWIN API server. Override it if your runtime uses a different endpoint.
