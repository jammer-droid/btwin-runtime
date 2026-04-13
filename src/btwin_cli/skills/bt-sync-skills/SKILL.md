---
name: bt:sync-skills
description: Use when the user wants to update installed B-TWIN skills on the current platform after new bt skills were added or changed in the repo
---

# Sync B-TWIN Skills

## Overview

Use this skill when the repo's bundled `bt:*` skills have changed and the user wants their current platform install to catch up.

This skill is specifically for **B-TWIN skill installation/update**, not for project data sync.

If the user means project/data synchronization rather than skill refresh, do not use this skill.

## What This Does

Re-run:

```bash
btwin install-skills --platform <platform>
```

or, for a project-local install:

```bash
btwin install-skills --local --platform <platform>
```

Because `btwin install-skills` installs the bundled public `bt-*` skills from the repo, running it again works as both:

- first-time install
- update/refresh after new skills are added

## When to Use

Use `bt:sync-skills` when:

- a new `bt:*` skill was added to the repo
- an existing bundled bt skill changed
- the user says things like "update bt skills", "sync skills", or "new bt skill isn't showing up"
- the installed platform copy may be stale relative to the repo

## Platform Mapping

Use the same platform the current client is running on:

| Platform | Command |
|---|---|
| Claude Code | `btwin install-skills --platform claude` |
| Codex | `btwin install-skills --platform codex` |
| Gemini | `btwin install-skills --platform gemini` |

If the skills were installed locally for the current project, add `--local`.

## Workflow

1. Confirm whether the user wants global or project-local skill sync
2. Identify the current platform
3. Run the matching `btwin install-skills` command
4. Report how many skills were installed and where
5. Tell the user to restart or refresh the client session if the platform caches skill lists

## Notes

- This updates only bundled **B-TWIN** skills, not arbitrary third-party agent skills.
- The install command is idempotent enough for normal refresh usage because it rewrites the installed skill links/targets.
- If the user is unsure whether their current install is global or local, prefer checking both the repo-local platform path and the home-directory platform path before reinstalling.

## Result Format

After running, report:
- which install command was used
- whether it was global or local
- the target platform
- the reported install path/count

Example:

```text
Synced B-TWIN skills.
- Command: btwin install-skills --platform codex
- Mode: global
- Result: 13 skills installed to ~/.agents/skills/
```
