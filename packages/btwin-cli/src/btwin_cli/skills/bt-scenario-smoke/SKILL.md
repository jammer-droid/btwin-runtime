---
name: bt:scenario-smoke
description: Use when a btwin CLI feature or helper workflow changed and needs real user-scenario validation through CLI commands before being called complete
---

# Smoke A B-TWIN User Scenario

## Overview

Use this skill after a meaningful `btwin` CLI change when tests pass but you still need proof that a real helper-first workflow works from the CLI surface.

The goal is not “one more unit test.” The goal is to run a short user scenario end to end and verify the JSON outputs that a real operator or agent would see.

## When to Use

Use `bt:scenario-smoke` when:

- a helper-first CLI command was added or changed
- a runtime/helper flow was hardened and needs CLI-level confirmation
- attached/shared API behavior matters, not just standalone storage behavior
- the user asks for “real scenario”, “user flow”, “smoke”, or “end-to-end-ish” validation

Do not use this as a replacement for normal tests. Run the repo test suite first.

## Default Environment

Prefer the isolated attached environment so the smoke does not touch the primary `~/.btwin`.

Default bootstrap:

```bash
scripts/bootstrap_isolated_attached_env.sh start --root "$TMP_ROOT" --project-root "$TMP_PROJECT" --project btwin-scenario --port "$PORT"
source "$TMP_ROOT/env.sh"
```

Run all `btwin` commands from that sourced shell so `BTWIN_CONFIG_PATH`, `BTWIN_DATA_DIR`, and `BTWIN_API_URL` stay aligned.

When the changed flow involves attached runtime, hooks, recovery, resume, or app-server behavior, also open a second terminal in the same repo and same sourced env for monitoring:

```bash
source "$TMP_ROOT/env.sh"
uv run btwin hud
```

Use the HUD to select the target thread or run `uv run btwin hud --thread <thread_id>` when the thread is already known.

## Scenario Pattern

Choose the smallest scenario that exercises the changed surface, but prefer real command sequences over one-off probes.

Common sequence:

1. create agents if the flow needs participants
2. create a thread with a real protocol
3. send at least one message
4. inspect `thread inbox` or `agent inbox`
5. if runtime helpers changed, run `runtime bind/current/clear`
6. if protocol helpers changed, run `protocol next` and `protocol apply-next`
7. if attached runtime behavior changed, monitor the same thread in `btwin hud`
8. verify final thread/runtime state with JSON output

## Good Scenario Families

- Thread lifecycle: `thread create -> show/list -> close`
- Inbox flow: `thread send-message -> thread inbox -> agent inbox -> ack-message`
- Runtime helper flow: `runtime bind -> runtime current -> protocol next/apply-next -> runtime clear`
- Runtime recovery flow: `thread create -> live attach -> induce fallback/recovery -> hud verify runtime events -> final status check`
- Protocol authoring and managed subagent flow: `btwin protocol scaffold -> validate -> preview -> create -> thread create -> delegate start -> contribution submit -> protocol apply-next -> delegate start -> contribution submit with executor metadata`.

For managed subagent scenarios, assert JSON-visible facts, including:

- preview shows `managed_agent_subagent` for the target role
- `spawn_packet.packet_type` is `btwin.managed_agent_subagent.dispatch`
- the parent inbox contains the generated dispatch packet
- the contribution preserves executor metadata
- tool policy remains declared unless runtime enforcement is explicitly under test

The compact flow name for this family is: `scaffold -> validate -> preview -> create`.

If attached direct delivery is not the behavior under test, prefer `--delivery-mode broadcast`.
In attached mode, direct delivery can require the target agent to already be active in that thread runtime.

## Verification Rules

- Prefer `--json` and assert on returned fields, not just exit code
- Verify the command that reflects the final user-visible state, not only intermediate commands
- When HUD is part of the smoke, record the runtime/workflow event sequence you observed there, not only the final command output
- Record any product constraint separately from real failures
- Report the exact commands used and the scenario outcome

## Result Format

After the smoke, report:

- scenario name in one line
- commands exercised
- key JSON facts verified
- key HUD facts verified
- any known constraints discovered

Example:

```text
Scenario smoke passed: attached review handoff flow
- Commands: agent create, thread create, thread send-message, agent inbox, runtime bind/current, protocol next/apply-next, runtime clear
- Verified: pending_message_count=1, current_phase=discussion, runtime binding cleared
- HUD: CODEX -> BTWIN Stop check requested, BTWIN -> CODEX Stop blocked, contribution recorded, Stop allowed
- Constraint: attached direct delivery requires active target runtime in the thread
```
