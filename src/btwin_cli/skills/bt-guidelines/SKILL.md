---
name: bt:guidelines
description: Use when the agent needs to review orchestration rules such as state transitions, tag conventions, or handoff policies
---

# Orchestration Guidelines

## Overview
Reads the orchestration guidelines file that defines workflow rules, state transitions, tag conventions, and handoff policies.

## Execution
This skill reads a file rather than calling an API endpoint.

```bash
BTWIN_DATA_DIR="${BTWIN_DATA_DIR:-$HOME/.btwin}"
cat "$BTWIN_DATA_DIR/global/orchestration-guidelines.md" 2>/dev/null
```

Or read directly if the active data directory is known:
```bash
cat <data_dir>/global/orchestration-guidelines.md
```

If the current runtime uses a non-default data directory, prefer the active B-TWIN config/runtime output over guessing.

## Response
Display the file contents to the user. Key sections: principles, workflow rules, state transitions, tag conventions, handoff, review.

## Notes
- Read-only. Direct the user to edit the file manually if changes are needed.
- Core guidelines from `btwin_get_guidelines` still apply as the base ruleset.
- `BTWIN_DATA_DIR` defaults to the standard local B-TWIN data directory and can be overridden when needed.
