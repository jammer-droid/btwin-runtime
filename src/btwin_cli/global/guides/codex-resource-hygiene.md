# Codex Resource Hygiene

This guide covers Codex-specific operational issues that do not belong in the product README, especially long-running sessions that accumulate stale MCP workers or hit local file-descriptor limits.

For delegation and subagent cleanup policy, see [`global/orchestration-guidelines.md`](../orchestration-guidelines.md).

## Symptoms

Typical failure signs:

- `Too many open files (os error 24)`
- MCP startup failures after long-running sessions
- repeated `pencil` MCP workers
- delegated-agent sessions finishing but not being cleaned up

## Quick Health Check

```bash
bash scripts/codex_fd_guard.sh status
```

This reports:

- current soft file-descriptor limit
- current `launchctl` `maxfiles` values
- active Codex parent processes
- live `pencil` MCP workers under each Codex process
- stale external agent and MCP counts

## Safe Cleanup

```bash
bash scripts/codex_fd_guard.sh cleanup
```

This cleanup is intentionally conservative:

- removes orphaned external ACP wrapper processes
- removes orphaned ACP node workers
- removes stale `antigravity` `pencil` MCP workers
- removes stale VS Code `pencil` MCP workers that are not owned by a live Codex process

It is designed to preserve active Codex sessions.

## Starting Codex With a Higher Soft Limit

If you launch Codex from a terminal, prefer:

```bash
bash scripts/run_codex_high_ulimit.sh
```

Override the default target if needed:

```bash
CODEX_OPEN_FILES_LIMIT=4096 bash scripts/run_codex_high_ulimit.sh
```

## macOS Limit Caveat

This repository can help clean up stale workers, but it cannot raise the GUI app's `launchd` limit by itself.

Important implications:

- terminal-launched Codex can inherit a higher `ulimit -n`
- `Codex.app` still depends on the macOS user-session `maxfiles` configuration
- on this machine, `launchctl limit maxfiles ...` was not permitted from an unprivileged repo script

If `bash scripts/codex_fd_guard.sh status` still shows `fd soft limit: 256`, long sessions with many MCP restarts remain at risk.

## Operational Advice

- Treat subagents, waits, MCP workers, and provider sessions as limited resources.
- Close delegated agents after their output has been integrated, unless more work is already queued for the same agent.
- Prefer reusing an existing relevant agent over spawning duplicates for the same unresolved task.
- If a task does not need design tooling, disable or avoid the `pencil` MCP for that session.
- If open-file pressure appears, reduce delegation fan-out before spawning more workers.
