# Orchestration Guidelines

This guideline follows all rules from btwin_get_guidelines as its base.

## Usage

Orchestration features are accessed through **Skills** (`/bt:*` commands).

This file covers **workflow/orchestration Skills only**.
For durable memory operations, use btwin MCP tools.
For the standardized "save this conversation/progress" helper, use `/bt:save` from the installed Skill set.

**Installation:** `btwin install-skills --platform claude` (or codex, gemini)

**Key commands:**
- `/bt:register` — Register agent
- `/bt:create` — Create workflow
- `/bt:list` — List workflows
- `/bt:status` — Workflow details
- `/bt:update` — Update task status
- `/bt:health` — Health check
- `/bt:next` — Start next task from queue
- `/bt:queue` — Queue a task

## Core Principles

- **Core guidelines first**: Follow all rules from `btwin_get_guidelines` (absolute paths, TLDR required, date formats, etc.)
- **No core modification**: Orchestration is a layer on top of btwin core. Do not directly modify core storage, indexer, or frontmatter schema.
- **Framework controls execution**: When using workflows, btwin framework owns task execution and state management. Agents must follow the framework's state transition rules.

## Workflow Rules

### Creation
- Workflow names should be specific ("API endpoint refactoring" instead of "work")
- Task names should be action-oriented ("Implement JWT auth middleware" instead of "authentication")

### Sequential Execution (MVP)
- Only one task can be in_progress at a time
- Complete the current task before starting the next
- Cannot skip order (1→3 not allowed, must go 1→2→3)

### State Transitions

**Implemented:**
- WorkflowStatus: `active` → `completed` | `escalated` | `cancelled`
- TaskStatus: `pending` → `in_progress` → `done` | `blocked` | `escalated`

**Not implemented (future):**
- RunStatus: `queued` → `running` → `completed` | `blocked` | `interrupted` | `cancelled`
- Phase: `implement` → `review` → `fix` → `review` (cycle)

### blocked vs escalated
- `blocked`: Waiting on external dependency (another task completion, external resource, etc.). Can resume when condition is met.
- `escalated`: Requires human intervention. When a task is escalated, the workflow also transitions to escalated.

## Agents and Workflows

### Registration

- **Register before workflow/orchestration operations** such as `/bt:next`, `/bt:queue`, or task assignment flows.
- Use the `/bt:register` skill command (or `POST /api/agents/register` HTTP API).
- Registration manages workflow identity and queue/task ownership.
- Current memory save flows do **not** automatically map registration to `contributors` on every MCP/proxy write path.
- Example: `/bt:register` → calls `POST /api/agents/register` with `{"name": "claude-code", "model": "claude-opus-4-6", "alias": "Claude"}`
- Registration is idempotent — same name re-registers with `last_seen` updated (upsert).
- If previous session had in_progress tasks (e.g., force quit), re-registration warns about them.
- To unregister: `/bt:unregister` skill command (or `DELETE /api/agents/{name}`) — warns about incomplete tasks before removal.

### State Inference
- Agent state is inferred from assigned tasks (no separate heartbeat/PID)
- `working`: Has an in_progress task
- `idle`: All assigned tasks are done or none assigned
- `registered`: Only registered, no tasks

### Task Assignment
- Tasks are assigned to agents via dashboard or HTTP API
- `PATCH /api/workflows/{workflow_id}/tasks/{task_id}/agent` → `{ "agent": "agent-name" }`

### Delegation and Resource Hygiene

- Treat spawned subagents, waits, MCP workers, and provider sessions as scarce resources, not free background state.
- After a delegated subagent finishes, the parent agent must promptly review the result, integrate or respond to it, and close the subagent if no further work is needed.
- Do not leave completed subagents open "just in case." Reuse an existing live subagent only when more work is actually queued for that same context.
- Before spawning a new subagent, check whether an equivalent live subagent already exists and can be reused. Avoid duplicate spawns for the same unresolved work.
- A `wait` is temporary coordination, not a resting state. After a wait returns, the parent agent should either continue the workflow, explicitly re-queue follow-up work, or close the delegated agent/session.
- If delegated work is abandoned, times out, or becomes irrelevant, clean up the related agent/session and mark the task state clearly (`blocked`, `escalated`, or reassigned) instead of silently leaving orphaned work behind.
- After force quit or crash recovery, agents should check for stale delegated work before spawning fresh subagents. Prefer resume-or-close over spawning duplicates.
- When repeated delegation causes lingering workers or open-file pressure, reduce fan-out, reuse active agents, and clean stale workers before continuing.

### Re-registration After Force Quit
- If the agent had in_progress tasks from a previous session, a warning is returned on re-registration
- Agent must resume or block the task

## HTTP API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/workflows` | Create workflow |
| GET | `/api/workflows` | List workflows |
| GET | `/api/workflows/{workflow_id}` | Get workflow details |
| PATCH | `/api/workflows/{workflow_id}/status` | Update workflow status |
| GET | `/api/workflows/{workflow_id}/timeline` | Get timeline |
| GET | `/api/workflows/{workflow_id}/tasks/{task_id}` | Get task details |
| PATCH | `/api/workflows/{workflow_id}/tasks/{task_id}/status` | Update task status |
| PATCH | `/api/workflows/{workflow_id}/tasks/{task_id}/agent` | Assign agent |
| GET | `/api/workflows/health` | Health check |
| GET | `/api/agents` | List registered agents |
| GET | `/api/agents/{name}/tasks` | Get tasks by agent |

## Tag Conventions

**Implemented:**
- `wf-type:{epic|task}` — Record type
- `wf-id:{id}` — Parent workflow identifier
- `wf-status:{active|pending|done|blocked|escalated}` — Current status

**Not implemented (future):**
- `wf-type:{run|handoff|review}` — Run/handoff/review records
- `wf-phase:{implement|review|fix}` — Current phase of a run

## Relations

- `derived_from` — Hierarchical relationship (epic ← task ← run)
- `related_records` — Reference relationship (run ↔ handoff, run ↔ review)

## Handoff (Not Implemented)

- Create a HandoffRecord when handing off work between agents
- The summary field must describe current state and next steps

## Review (Not Implemented)

- verdict: approve or request_fix
- On request_fix, transition to fix phase; re-review after fix

## Storage Path

```
entries/workflow/{date}/{record_id}.md
```

Project info is stored only in the frontmatter `source_project` field (not in the path).
