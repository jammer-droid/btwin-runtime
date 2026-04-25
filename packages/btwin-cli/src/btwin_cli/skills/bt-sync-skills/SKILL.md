---
name: bt:sync-skills
description: Use when the user wants to refresh installed B-TWIN skills after a repo update, and also needs any changed btwin binary/service state to catch up
---

# Sync B-TWIN Skills

## Overview

Use this skill when the repo was updated and the user wants their current platform install to catch up.

This includes two related refresh paths:

- refreshing bundled `bt:*` skills
- refreshing the installed `btwin` executable and service/session state when the pull changed CLI/runtime code

This skill is specifically for **B-TWIN post-pull refresh**, not for project data sync.

If the user means project/data synchronization rather than skill refresh, do not use this skill.

## What This Does

For first-time global setup or when the overall Codex-facing install may be stale,
prefer:

```bash
btwin init
```

For a narrower bundled-skill relink only, re-run:

```bash
btwin install-skills --platform <platform>
```

or, for a project-local install:

```bash
btwin install-skills --local --platform <platform>
```

Because `btwin install-skills` installs the bundled public `bt-*` skills from the repo,
running it again works as a compatibility refresh path after new skills are added.

When the repo pull also changed the packaged CLI/runtime code, this skill should additionally refresh the installed `btwin` executable before syncing skills.

## When to Use

Use `bt:sync-skills` when:

- the user just ran `git pull` or otherwise updated the repo and wants the install to catch up
- a new `bt:*` skill was added to the repo
- an existing bundled bt skill changed
- the user says things like "update bt skills", "sync skills", or "new bt skill isn't showing up"
- the installed platform copy or `btwin` binary may be stale relative to the repo

## Platform Mapping

Use the same platform the current client is running on:

| Platform | Command |
|---|---|
| Claude Code | `btwin install-skills --platform claude` |
| Codex | `btwin install-skills --platform codex` |
| Gemini | `btwin install-skills --platform gemini` |

If the skills were installed locally for the current project, add `--local`.

## Workflow

1. Confirm whether the user wants global or project-local refresh
2. Identify the current platform
3. If the repo was just updated, inspect whether the pulled changes touched runtime/CLI packaging or only skills/docs
4. If runtime/CLI packaging changed and the user relies on an installed `btwin` binary, refresh the binary first
5. If the user runs `serve-api` via launchd on macOS and the executable changed, re-run `btwin service install`
6. Prefer `btwin init` for global refresh unless the user clearly wants the narrower compatibility relink path
7. If using the narrower path, run the matching `btwin install-skills` command
8. Tell the user to restart or refresh the client session if the platform caches skill lists or the MCP proxy may be stale

## Post-Pull Decision Rule

After a repo update, treat the refresh as **binary + skills** when the changed files include things like:

- `packages/btwin-cli/`
- `packages/btwin-core/`
- `pyproject.toml`
- lockfiles or packaging metadata that affect the installed executable
- runtime bootstrap/service files that change how the installed executable is launched

Treat it as **skills-only** when the pull changed only bundled skill files or skill-adjacent docs.

If the user just pulled and the change scope is unclear, prefer checking the changed files first rather than blindly reinstalling everything.

## Binary Refresh Commands

When the installed `btwin` binary must catch up with the pulled repo, prefer:

```bash
uv tool install -e . --force
```

If the user works repo-locally with `uv run btwin ...` and does not rely on a separately installed `btwin` executable, a binary reinstall is usually unnecessary.

If the user runs the macOS background service from the installed executable, follow the binary refresh with:

```bash
btwin service install
```

Then refresh the setup:

Preferred:

```bash
btwin init
```

Compatibility relink only:

```bash
btwin install-skills --platform <platform>
```

## Notes

- `btwin init` is the preferred global setup/refresh path.
- `btwin install-skills` remains a narrower compatibility relink command.
- This workflow updates bundled **B-TWIN** skills and, when needed, the installed `btwin` executable used by those skills.
- The install command is idempotent enough for normal refresh usage because it rewrites the installed skill links/targets.
- If the user is unsure whether their current install is global or local, prefer checking both the repo-local platform path and the home-directory platform path before reinstalling.
- Restart or reconnect the client after refresh if it may still be holding a stale MCP proxy or stale skill list.

## Result Format

After running, report:
- whether this was skills-only or binary+skills refresh
- which install command was used
- whether a binary refresh was required
- whether service reinstall/restart guidance was applied
- whether it was global or local
- the target platform
- the reported install path/count

Example:

```text
Synced B-TWIN post-pull refresh.
- Refresh type: binary + skills
- Binary: uv tool install -e . --force
- Service: btwin service install
- Command: btwin install-skills --platform codex
- Mode: global
- Result: 13 skills installed to ~/.codex/skills/
- Next: restart Codex so it reconnects with the refreshed btwin MCP proxy
```
