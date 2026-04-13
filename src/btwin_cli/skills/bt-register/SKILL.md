---
name: bt:register
description: Use when an agent needs to join the orchestration system before starting workflow tasks
---

# Register Agent

## Overview
Registers an agent with the btwin orchestration system. Must be called before queue/task operations (bt:next, bt:queue, bt:update, etc.).

## API Call
```bash
BTWIN_API_URL="${BTWIN_API_URL:-http://localhost:8787}"
curl -s -X POST "$BTWIN_API_URL/api/agents/register" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "<agent-name>",
    "model": "<model-name>",
    "alias": "<optional-alias>",
    "capabilities": ["<model1>", "<model2>"]
  }'
```

## Parameters
- `name` (required): Unique agent name, kebab-case (e.g., `claude-code-1`)
- `model` (required): Model name (e.g., `claude-sonnet-4-20250514`)
- `alias` (optional): Human-readable display name
- `capabilities` (optional): List of models this agent can use

## Response
**Success (200):** Returns `name`, `model`, `in_progress_tasks[]`. If `in_progress_tasks` is non-empty, inform user of previously running tasks.

**Failure (422):** Missing required fields. Check `name` and `model`.

## Notes
- Re-registering the same name performs an upsert (updates existing record).
- Name must be unique across the system.
- `BTWIN_API_URL` defaults to the local B-TWIN API server. Override it if your runtime uses a different endpoint.
