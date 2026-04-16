# Unified Test Runner Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a unified `pytest`-backed test runner that manages common test groups, writes per-run HTML/artifact output, and supports opt-in provider smoke with a fixed `app-server` + `gpt-5.4-mini` profile.

**Architecture:** Keep `pytest` as the execution and assertion engine, and add a thin `scripts/run_tests.py` wrapper for policy. The wrapper will create a run artifact directory, select markers or test targets, attach `pytest-html`, enforce retention, and gate provider smoke behind explicit opt-in and environment preflight checks.

**Tech Stack:** Python 3.11, pytest, pytest-html, existing btwin CLI/core test helpers, isolated attached env bootstrap script

---

### Task 1: Add pytest-html and marker policy

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`
- Test: `tests/test_run_tests_script.py`

**Step 1: Write the failing test**

```python
def test_pytest_markers_are_registered(pytester):
    result = pytester.runpytest("--markers")
    result.stdout.fnmatch_lines(["*provider_smoke*", "*cli_smoke*"])
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_pytest_markers_are_registered -v`
Expected: FAIL because the marker registration helper or test support does not exist yet.

**Step 3: Write minimal implementation**

- Add `pytest-html` to the dev dependency group in `pyproject.toml`
- Register test markers in `tests/conftest.py`
- Keep the marker set minimal: `unit`, `integration`, `cli_smoke`, `provider_smoke`

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_pytest_markers_are_registered -v`
Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/test_run_tests_script.py
git commit -m "test: register unified test markers"
```

### Task 2: Create the run_tests entrypoint with artifact directories

**Files:**
- Create: `scripts/run_tests.py`
- Test: `tests/test_run_tests_script.py`

**Step 1: Write the failing test**

```python
def test_run_tests_creates_run_directory_and_latest_link(tmp_path):
    result = run_runner(["unit", "--artifact-root", str(tmp_path)])
    assert result.returncode == 0
    assert (tmp_path / "latest").exists()
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_run_tests_creates_run_directory_and_latest_link -v`
Expected: FAIL because `scripts/run_tests.py` does not exist.

**Step 3: Write minimal implementation**

- Implement `scripts/run_tests.py`
- Support groups: `unit`, `integration`, `cli-smoke`, `provider-smoke`, `all`
- Create `.test-artifacts/<timestamp-group>/`
- Write `metadata.json`
- Point `.test-artifacts/latest` to the newest run
- Invoke `pytest` with `pytest-html` output to `report.html`

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_run_tests_creates_run_directory_and_latest_link -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/run_tests.py tests/test_run_tests_script.py
git commit -m "test: add unified pytest runner"
```

### Task 3: Add retention policy and metadata capture

**Files:**
- Modify: `scripts/run_tests.py`
- Test: `tests/test_run_tests_script.py`

**Step 1: Write the failing test**

```python
def test_run_tests_prunes_old_runs_and_keeps_configured_count(tmp_path):
    seed_run_directories(tmp_path, count=35)
    result = run_runner(["unit", "--artifact-root", str(tmp_path), "--keep-runs", "30"])
    assert result.returncode == 0
    assert count_run_directories(tmp_path) == 30
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_run_tests_prunes_old_runs_and_keeps_configured_count -v`
Expected: FAIL because pruning is not implemented.

**Step 3: Write minimal implementation**

- Add retention cleanup to `scripts/run_tests.py`
- Use priority order: CLI option, `BTWIN_TEST_KEEP_RUNS`, default `30`
- Record selected retention and group info into `metadata.json`

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_run_tests_prunes_old_runs_and_keeps_configured_count -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/run_tests.py tests/test_run_tests_script.py
git commit -m "test: add artifact retention to test runner"
```

### Task 4: Gate provider smoke behind explicit opt-in and preflight checks

**Files:**
- Modify: `scripts/run_tests.py`
- Test: `tests/test_run_tests_script.py`

**Step 1: Write the failing test**

```python
def test_provider_smoke_skips_when_provider_preflight_is_unavailable(tmp_path):
    result = run_runner(["provider-smoke", "--artifact-root", str(tmp_path)])
    assert result.returncode == 0
    assert "SKIPPED" in result.stdout
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_provider_smoke_skips_when_provider_preflight_is_unavailable -v`
Expected: FAIL because provider preflight logic does not exist.

**Step 3: Write minimal implementation**

- Add provider-specific preflight to `scripts/run_tests.py`
- Check for provider CLI availability and local auth readiness
- Only allow provider smoke when the group is explicitly selected
- When preflight fails, run pytest in a way that reports skip instead of hard failure

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_provider_smoke_skips_when_provider_preflight_is_unavailable -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/run_tests.py tests/test_run_tests_script.py
git commit -m "test: gate provider smoke behind preflight checks"
```

### Task 5: Build provider smoke fixtures for isolated attached env

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_provider_smoke_runner.py`
- Reuse: `scripts/bootstrap_isolated_attached_env.sh`

**Step 1: Write the failing test**

```python
@pytest.mark.provider_smoke
def test_provider_smoke_fixture_exports_isolated_env(provider_smoke_env):
    assert provider_smoke_env["BTWIN_API_URL"].startswith("http://127.0.0.1:")
    assert provider_smoke_env["BTWIN_DATA_DIR"]
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_provider_smoke_runner.py::test_provider_smoke_fixture_exports_isolated_env -v`
Expected: FAIL because the fixture does not exist.

**Step 3: Write minimal implementation**

- Add a fixture that boots the isolated attached env for provider smoke
- Capture artifact paths for command logs and snapshots
- Keep cleanup explicit and deterministic

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_provider_smoke_runner.py::test_provider_smoke_fixture_exports_isolated_env -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/conftest.py tests/test_provider_smoke_runner.py
git commit -m "test: add provider smoke environment fixture"
```

### Task 6: Add the scripted initial-prompt provider smoke path

**Files:**
- Modify: `tests/test_provider_smoke_runner.py`
- Reuse: `packages/btwin-core/src/btwin_core/prototypes/persistent_sessions/codex_app_server_adapter.py`
- Reuse: `packages/btwin-core/src/btwin_core/prototypes/persistent_sessions/types.py`

**Step 1: Write the failing test**

```python
@pytest.mark.provider_smoke
def test_provider_smoke_runs_scripted_thread_flow(provider_smoke_env):
    result = run_scripted_provider_smoke(provider_smoke_env)
    assert result["requested_model"] == "gpt-5.4-mini"
    assert result["thread_state"]["contributions"]
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_provider_smoke_runner.py::test_provider_smoke_runs_scripted_thread_flow -v`
Expected: FAIL because the scripted thread flow helper does not exist.

**Step 3: Write minimal implementation**

- Create a helper that:
  - creates a test thread/protocol
  - attaches Codex `app-server`
  - injects a fixed initial prompt
  - captures thread state, workflow events, and runtime metadata
- Hardcode the default provider profile to `app-server` long-term + `gpt-5.4-mini`
- Record both `requested_model` and `effective_model` in provider artifacts

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_provider_smoke_runner.py::test_provider_smoke_runs_scripted_thread_flow -v`
Expected: PASS in a locally authenticated provider environment, or SKIP when preflight is unavailable.

**Step 5: Commit**

```bash
git add tests/test_provider_smoke_runner.py
git commit -m "test: add scripted provider smoke flow"
```

### Task 7: Wire group selection and docs

**Files:**
- Modify: `scripts/run_tests.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Test: `tests/test_run_tests_script.py`

**Step 1: Write the failing test**

```python
def test_run_tests_all_group_excludes_provider_by_default(tmp_path):
    result = run_runner(["all", "--artifact-root", str(tmp_path)])
    assert "provider_smoke" not in result.stdout
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_run_tests_all_group_excludes_provider_by_default -v`
Expected: FAIL because the group policy is not finalized.

**Step 3: Write minimal implementation**

- Finalize group-to-marker resolution in `scripts/run_tests.py`
- Document:
  - common runner usage
  - artifact location
  - retention policy
  - provider smoke opt-in rules
  - fixed provider model and profile

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_run_tests_script.py::test_run_tests_all_group_excludes_provider_by_default -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/run_tests.py README.md AGENTS.md tests/test_run_tests_script.py
git commit -m "docs: document unified test runner usage"
```

### Task 8: Verify the rollout end to end

**Files:**
- Verify only; no new source files required

**Step 1: Run fast groups**

Run: `uv run python scripts/run_tests.py unit`
Expected: PASS and `.test-artifacts/latest/report.html` exists

**Step 2: Run integration group**

Run: `uv run python scripts/run_tests.py integration`
Expected: PASS and a new run directory is created

**Step 3: Run CLI smoke group**

Run: `uv run python scripts/run_tests.py cli-smoke`
Expected: PASS and run metadata correctly identifies the group

**Step 4: Run provider smoke in a local authenticated environment**

Run: `uv run python scripts/run_tests.py provider-smoke`
Expected: PASS or SKIP with explicit preflight reason, and provider artifacts include `requested_model=gpt-5.4-mini`

**Step 5: Commit**

```bash
git add .
git commit -m "test: verify unified runner rollout"
```
