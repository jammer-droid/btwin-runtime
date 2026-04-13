---
name: bt:health
description: Use when the agent needs a quick check on overall workflow health or to find problematic workflows
---

# Workflow Health

## Overview
Performs a health check across all workflows, surfacing any issues that need attention.

## API Call
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s "$BTWIN_API_URL/api/workflows/health"
```

No parameters.

## Response
**Healthy (200):** `{ok: true, scope: "workflows", status: "healthy", issues: []}`.

**Issues found (200):** `{ok: false, status: "issues_found", issues: [...]}`. Each issue has `type`, `workflow_id`, `name`, `detail`.

**Issue types and actions:**
- `escalated`: Needs human intervention. Prompt user for input.
- `blocked`: Task is blocked. Use `bt:status` to investigate.
- `stalled`: Active workflow with no in-progress task. Use `bt:status` then start the next task.

## Notes
- This is a quick overview. Use `bt:status` for detailed investigation.
- `BTWIN_API_URL` defaults to the local B-TWIN API server. Override it if your runtime uses a different endpoint.
