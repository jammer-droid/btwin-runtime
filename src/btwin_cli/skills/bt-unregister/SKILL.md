---
name: bt:unregister
description: Use when an agent is done with all work and needs to leave the orchestration system
---

# Unregister Agent

## Overview
Removes an agent from the btwin orchestration system. Typically used for session cleanup when an agent no longer participates in workflows.

## API Call
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s -X DELETE "$BTWIN_API_URL/api/agents/<agent-name>"
```

## Parameters
- `<agent-name>` (required): Agent name in URL path

## Response
**Success (200):** Returns `{removed: true, warnings: []}`. If `warnings` is non-empty (e.g., in-progress tasks exist), display them to the user.

**Failure (404):** Agent not found. Verify agent name.

## Notes
- If the agent has in-progress tasks, warnings will list them. Complete or reassign tasks before unregistering.
- Agent activity records are preserved after unregistration.
- `BTWIN_API_URL` defaults to the local B-TWIN API server. Override it if your runtime uses a different endpoint.
