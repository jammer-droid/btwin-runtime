# test-env CLI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a first-class `btwin test-env` CLI surface so users can prepare and inspect an isolated Codex-capable test environment with commands like `btwin test-env up` and `btwin test-env hud`.

**Architecture:** Keep the global runtime model unchanged. Build a thin CLI layer that reuses the isolated bootstrap logic internally, resolves a dedicated test root/project root, manages only the test env’s owned `serve-api`, and prepares a test project workspace with local Codex config and a test-env-specific `AGENTS.md`.

**Tech Stack:** Python Typer CLI, existing btwin CLI/runtime code, Bash bootstrap helper reuse, pytest

---

### Task 1: Add internal test-env resolution helpers

**Files:**
- Modify: `packages/btwin-cli/src/btwin_cli/main.py`
- Test: `tests/test_bootstrap_isolated_attached_env.py` or new focused test file if cleaner

**Step 1: Write the failing test**

Add a focused test for the internal resolution rules that the future `test-env` commands will depend on.

At minimum cover:

- default test root is deterministic and separate from global runtime state
- current worktree `.venv/bin/btwin` is preferred when present
- `PATH` fallback is used when no worktree-local binary exists

If helper extraction makes more sense in a new test file, create one such as:

```python
def test_test_env_resolution_prefers_repo_local_btwin(...):
    ...
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run python -m pytest tests/test_bootstrap_isolated_attached_env.py -q
```

Or the new focused file if created.

Expected: FAIL because the resolution helpers do not yet exist.

**Step 3: Write minimal implementation**

Add internal helpers in `main.py` to compute:

- test env root
- test project root
- preferred `btwin` executable
- owned API URL / PID paths

Keep this logic private to the CLI for now.

**Step 4: Run test to verify it passes**

Run the targeted pytest command again.

Expected: PASS

**Step 5: Commit**

```bash
git add packages/btwin-cli/src/btwin_cli/main.py tests/...
git commit -m "feat: add test-env resolution helpers"
```

### Task 2: Add `btwin test-env up` and `status`

**Files:**
- Modify: `packages/btwin-cli/src/btwin_cli/main.py`
- Test: new focused test file under `tests/` for test-env commands

**Step 1: Write the failing test**

Add tests for:

- `btwin test-env up` prepares isolated config/data/project state
- local `.codex/config.toml` is created in the test project root
- test project root gets a test-env-specific `AGENTS.md`
- `btwin test-env status` reports the expected root/project/API info

Use CLI runner style tests that inspect resulting files and printed output.

**Step 2: Run test to verify it fails**

Run the targeted test file.

Expected: FAIL because the `test-env` command group does not yet exist.

**Step 3: Write minimal implementation**

Add a Typer sub-app or command group:

- `btwin test-env up`
- `btwin test-env status`

Implementation requirements:

- prepare isolated root and project root
- reuse bootstrap logic where practical rather than duplicating everything
- create local `.codex/config.toml`
- create test-env-specific `AGENTS.md` in the test project root only
- never mutate the current repo’s `AGENTS.md`
- start or reuse only the owned test `serve-api`

**Step 4: Run test to verify it passes**

Run the targeted pytest command again.

Expected: PASS

**Step 5: Commit**

```bash
git add packages/btwin-cli/src/btwin_cli/main.py tests/...
git commit -m "feat: add test-env up and status commands"
```

### Task 3: Add `btwin test-env hud` and `down`

**Files:**
- Modify: `packages/btwin-cli/src/btwin_cli/main.py`
- Test: same focused test file under `tests/`

**Step 1: Write the failing test**

Add tests for:

- `btwin test-env hud` resolves the same isolated env and invokes HUD against it
- `btwin test-env down` stops only the owned test `serve-api`
- `down` does not target unrelated/global processes

Where direct interactive HUD assertions are impractical, assert the command wiring and owned-process semantics.

**Step 2: Run test to verify it fails**

Run the targeted pytest command again.

Expected: FAIL because `hud` / `down` commands are not yet implemented.

**Step 3: Write minimal implementation**

Add:

- `btwin test-env hud`
- `btwin test-env down`

Implementation requirements:

- `hud` must produce the same interactive HUD behavior as `btwin hud`, but scoped to the resolved test env
- `down` must only stop the test env’s owned API
- do not auto-create sample threads or agents

**Step 4: Run test to verify it passes**

Run the targeted pytest command again.

Expected: PASS

**Step 5: Commit**

```bash
git add packages/btwin-cli/src/btwin_cli/main.py tests/...
git commit -m "feat: add test-env hud and down commands"
```

### Task 4: Update docs to prefer `btwin test-env`

**Files:**
- Modify: `README.md`

**Step 1: Write the failing doc expectation**

Document the gap:

```text
- README still teaches env.sh/helper-first isolated testing as the main path
- README does not yet present btwin test-env as the preferred user surface
```

**Step 2: Confirm the gap**

Run:

```bash
rg -n "test-env|env.sh|btwin_test_up|btwin_test_hud" README.md
```

Expected: `btwin test-env` is absent or not yet the primary recommendation.

**Step 3: Write minimal documentation changes**

Update README so the recommended path becomes:

```bash
btwin test-env up
btwin test-env hud
```

Also mention:

- test project root location
- that Codex should be launched from the test project root for the real user scenario
- the current repo’s `AGENTS.md` is not modified

Keep legacy helper details only as secondary/internal guidance if still needed.

**Step 4: Verify the wording**

Run:

```bash
rg -n "btwin test-env up|btwin test-env hud|Codex|AGENTS.md|global" README.md
```

Expected: README now presents the new surface clearly.

**Step 5: Commit**

```bash
git add README.md
git commit -m "docs: prefer test-env cli workflow"
```

### Task 5: Run a real Codex-oriented scenario smoke

**Files:**
- Verify only: `packages/btwin-cli/src/btwin_cli/main.py`
- Verify only: generated test project root contents

**Step 1: Prepare the test env**

Run:

```bash
btwin test-env up
btwin test-env status
```

Expected: isolated API is ready, project root exists, local Codex config and test-env `AGENTS.md` are present.

**Step 2: Verify HUD path**

Run:

```bash
btwin test-env hud
```

Expected: interactive HUD opens against the test env rather than the global runtime.

**Step 3: Verify Codex-oriented workspace**

From the reported test project root, confirm:

- `.codex/config.toml` exists
- `AGENTS.md` contains test-env-specific guidance

If practical in the current session, launch `codex` from that root and confirm it would see the isolated workspace context. If full interactive Codex launch is not practical, verify the workspace artifacts and explain the expected user path explicitly.

**Step 4: Record the smoke result**

Report:

```text
Scenario smoke passed: test-env cli workflow
- Commands: btwin test-env up, btwin test-env status, btwin test-env hud
- Verified: isolated serve-api was prepared separately from global runtime, HUD resolved against the test env, and the test project root contains local Codex config plus test-env-specific AGENTS.md
- Constraint: real end-user Codex thread creation still requires launching codex from the reported test project root
```

**Step 5: Commit**

If code/docs changed during smoke fixes:

```bash
git add <touched-files>
git commit -m "test: verify test-env cli smoke"
```

If no files changed, skip this commit.
