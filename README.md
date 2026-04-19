# btwin

Packaged runtime workspace for B-TWIN.

This repository keeps the runtime-facing packages together in one place:

- `packages/btwin-core`
- `packages/btwin-cli`

`btwin-core` owns the domain/runtime implementation.
`btwin-cli` provides the CLI, HTTP API, and MCP proxy surface on top of it.

The Codex provider implementation currently stays inside `btwin-core`, so there
is no separate provider package in this split.

## Package Guides

If you are working inside the repository, these package-level READMEs are the
best short references:

- [`packages/btwin-core/README.md`](packages/btwin-core/README.md) — core library APIs, data-dir expectations, and what the standalone package does not include
- [`packages/btwin-cli/README.md`](packages/btwin-cli/README.md) — `btwin` CLI package, HTTP API, MCP proxy, provider bootstrap, and bundled skills

## Runtime Model

The default B-TWIN operating model is:

```text
LLM Client
    ↓ stdio
btwin mcp-proxy
    ↓ HTTP
btwin serve-api
    ↓
~/.btwin/
```

Default assumptions:

- data lives in the global `~/.btwin` directory
- `serve-api` is the shared backend and is expected to stay available
- `mcp-proxy` is the lightweight bridge that MCP clients connect to
- on macOS, the intended steady-state is to keep `serve-api` running as a background LaunchAgent via `btwin service install`
- project-local `.btwin/` is an exception for isolated testing, not the default

### Data Dir Resolution

Most `btwin` commands decide which store to read and write from using this
precedence:

1. `BTWIN_DATA_DIR`
2. `./.btwin/` under the current working directory
3. `~/.btwin/`

In practice, that means a repo-local `.btwin/` can become the active runtime
store for normal CLI commands if you run `btwin` from that repository. Treat a
project-local `.btwin/` as temporary isolated runtime state, not as shared
project documentation or source-controlled data.

If you intentionally use a repo-local `.btwin/`, ignore it in git.

## Prerequisites

- Python 3.11+
- `uv`
- `codex` CLI

Install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Provider Model

Today B-TWIN ships with exactly one provider integration: Codex.

- `btwin init` currently supports Codex only
- the generated MCP config launches `btwin mcp-proxy` for Codex
- the default user workflow assumes Codex is both the MCP client and the active LLM provider surface

In practice, that means Codex is not an optional extra right now. If you want the
standard B-TWIN workflow, install the `codex` CLI first and then run `btwin init`.

## Recommended macOS Setup

If you are using macOS, the intended workflow is to keep `serve-api` running in
the background and let Codex connect through `btwin mcp-proxy`.

### First-Time Setup From a Fresh Clone

If you cloned this repository and want the shortest normal setup path for daily
Codex use on macOS, run:

```bash
git clone https://github.com/jammer-droid/btwin.git
cd btwin
./scripts/install_btwin_macos.sh
```

This one-shot installer wraps the normal first-time setup steps:

1. `uv sync`
2. `uv tool install -e .`
3. `btwin init`
4. `btwin service install`

After that, opening Codex in other repositories should reuse the same global
`btwin` install. You do not need to rerun `btwin init` for every repository.

Requirements before running the script:

- macOS
- `uv`
- `codex`

If you want to preview what the installer will do:

```bash
./scripts/install_btwin_macos.sh --dry-run
```

### Manual First-Time Setup

If you want the same setup steps individually for debugging or manual recovery,
use the detailed path below.

Install and verify the repo-local environment:

```bash
git clone https://github.com/jammer-droid/btwin.git
cd btwin
uv sync
uv run btwin --help
```

Install `btwin` as a normal CLI so Codex and launchd can call it directly:

```bash
cd btwin
uv tool install -e .
btwin --help
```

Using only `uv run btwin ...` from the repo clone is fine for development, but
it does not create the stable global CLI path that Codex MCP config and launchd
expect. For normal user setup, prefer `uv tool install -e .`.

Initialize the Codex provider config:

```bash
btwin init
```

This creates `~/.btwin/providers.json` and writes a Codex MCP entry that launches:

```toml
[mcp_servers.btwin]
command = "btwin"
args = ["mcp-proxy"]
```

Install and start the background service:

```bash
btwin service install
btwin service status
```

`btwin init` is now the preferred first-time global setup path. It registers the
Codex MCP entry and installs the bundled B-TWIN assets needed for the Codex-facing
workflow. `btwin install-skills --platform codex` remains available as a compatibility
refresh path when you only want to relink bundled skills.

`btwin init` handles the global Codex-facing setup. It does not pre-create
repo-local helper files for every repository. Those are bootstrapped lazily
when B-TWIN actually launches a managed helper inside a target repository.

For B-TWIN-managed helper sessions, the expected working model is:

- the helper `cwd` stays inside the target git repository
- Codex trusts that project, so project-scoped `.codex/` layers are active
- B-TWIN lazily bootstraps a repo-local helper overlay under `.btwin/helpers/<agent>/workspace`
- existing project `AGENTS.md` and `.codex/hooks.json` remain untouched
- B-TWIN adds deeper helper-scoped layers on top instead of rewriting user files

In practice, that means the first helper launch for a repository can create:

- `.btwin/helpers/<agent>/workspace/`
- `.btwin/helpers/<agent>/AGENTS.md`
- `.btwin/helpers/<agent>/.codex/hooks.json`

These files belong to the B-TWIN helper overlay. They are repo-local runtime
state, not source files, and should normally stay untracked.

If a helper session is launched outside the target repo, or inside a project that
Codex does not trust, project-scoped `AGENTS.md` / `.codex/` behavior is not
guaranteed.

If helper launch fails because the project is not trusted, trust that repository
in Codex first and then retry from inside the same git repo. Helper overlay
bootstrapping depends on Codex's normal project-scoped config behavior.

If the repository already defines hooks, they can affect helper behavior because
Codex loads matching hooks from multiple active layers. B-TWIN hook integrations
should be treated as additive; do not assume a deterministic hook ordering.

After setup, restart Codex so it reconnects with the new MCP config:

```bash
codex
```

After that, the normal daily workflow is:

1. keep `btwin serve-api` running in the background through launchd
2. let Codex connect via `btwin mcp-proxy`
3. use the global `~/.btwin` data directory

You should not need to run `btwin serve-api` manually in a terminal for normal use.

### Quick Setup Verification

After the first-time setup, these checks should succeed:

```bash
btwin --help
btwin service status
btwin init
```

And in `~/.codex/config.toml` you should see a `mcp_servers.btwin` entry that
launches `btwin mcp-proxy`.

### MCP Reconnect Troubleshooting

If `btwin serve-api` is running but an existing Codex session still shows
`Transport closed` or cannot call B-TWIN MCP tools, restart Codex in a fresh
session first. Existing Codex sessions can keep a stale MCP transport alive
even after `btwin init`, service restarts, or local config changes.

If the problem only happens in one repository, also check for a project-local
`.codex/config.toml` that overrides `mcp_servers.btwin`. A repo-local Codex
config takes precedence over the global `~/.codex/config.toml`, so a stale
project override can make Codex launch a different `btwin mcp-proxy` command
than the one you expect from the global setup.

## Local Development Setup

If you already have a repo clone with `uv sync` and only want a manual,
foreground workflow for development or debugging, use this path:

```bash
uv run btwin --help
```

Start the shared API manually:

```bash
uv run btwin serve-api
```

In another terminal, start the MCP proxy:

```bash
cd btwin
uv run btwin mcp-proxy
```

For a quick API health check:

```bash
curl -s http://localhost:8787/api/sessions/status
```

This flow is mainly for local development, smoke tests, and debugging from the
repo clone. It does not make `btwin` globally available to your shell or MCP client.

## Codex Session Safety

Long-running Codex sessions can accumulate stale MCP child processes and hit
`Too many open files (os error 24)`, especially when multiple Codex sessions,
subagents, or failed `/fork` attempts pile up on the same machine.

Recommended safety rules:

1. if you see `Too many open files`, `MCP startup failed`, or repeated session
   creation failures, stop retrying inside that session
2. save a handoff first, then restart in a fresh Codex session
3. keep parallel/background Codex sessions to the minimum needed

This repo includes a helper script for diagnosing and cleaning up that state:

```bash
scripts/codex_session_health.sh warn
scripts/codex_session_health.sh cleanup --pid <codex_pid> --dry-run
scripts/codex_session_health.sh install-local
```

`warn` is read-only and prints the current soft open-files limit together with
Codex, `btwin mcp-proxy`, and Pencil MCP process counts.

`cleanup` is intentionally bounded:

- `--pid <codex_pid>` targets one explicit Codex parent session and its child MCP processes
- `--orphans` targets only MCP children whose parent is no longer a live Codex session

After `install-local`, you can use personal helpers from `~/.local/bin`:

```bash
codex-safe-start
codex-health
codex-clean-stale --pid <codex_pid>
```

`codex-safe-start` raises the soft open-files limit for the launched Codex
process when possible before `exec`ing `codex`.

## macOS Background Service

This section is the detailed reference for the background service used in the
recommended macOS setup.

Once the service is installed, the main CLI controls are:

```bash
btwin service status
btwin service restart
btwin service stop
```

`btwin service install` writes `~/.btwin/com.btwin.serve-api.plist`, ensures
`~/.btwin/logs/` exists, links the plist into `~/Library/LaunchAgents/`, and
bootstraps the service with the current `btwin` executable found on `PATH`.

Unlike ordinary CLI/runtime state, the launchd helper always uses the global
`~/.btwin` service paths. A repo-local `.btwin/` should not become the backing
store for the background LaunchAgent.

If the active `btwin` executable changes later, run `btwin service install`
again to refresh the LaunchAgent target.

For a stable long-lived service, prefer running `btwin service install` after
you have installed `btwin` globally. If you run it from `uv run`, the LaunchAgent
may point at the repo-local `.venv/bin/btwin` path for that clone.

Manual `launchctl` flow is still available if you want it. If you already have
the standard plist at `~/.btwin/com.btwin.serve-api.plist`, you can load it
with:

```bash
mkdir -p ~/.btwin/logs
launchctl bootstrap gui/$(id -u) ~/.btwin/com.btwin.serve-api.plist
```

Useful service commands:

```bash
launchctl print gui/$(id -u)/com.btwin.serve-api
launchctl kickstart -k gui/$(id -u)/com.btwin.serve-api
launchctl bootout gui/$(id -u)/com.btwin.serve-api
tail -f ~/.btwin/logs/serve-api.stderr.log
```

Example plist target after a global install:

```xml
<array>
  <string>/Users/home/.local/bin/btwin</string>
  <string>serve-api</string>
</array>
```

## Codex / MCP Setup

This repository already contains the packaged runtime assets needed by:

- `btwin serve-api`
- `btwin mcp-proxy`
- bundled runtime docs
- bundled protocol definitions
- bundled skills

For repo-local development, prefer `uv run btwin ...` first.

If you have already installed `btwin` globally, other MCP clients can run:

```text
command: btwin
args: ["mcp-proxy"]
```

For Codex, `btwin init` is the canonical global setup path: it writes the
equivalent MCP entry, syncs bundled B-TWIN assets, and installs the bundled
skills. `btwin install-skills --platform codex` remains available as a
compatibility refresh command.

The bundled skills are short task-oriented guides installed into the client
environment. They are not part of `btwin-core`; they ship with `btwin-cli` and
cover common B-TWIN workflows such as save, handoff, scenario smoke, sync,
queue, and status.

If you replaced an older global `btwin` install with this runtime split, restart
your Codex/MCP client session after `btwin init`. Existing MCP proxy processes
may keep using the older environment until the client reconnects.

## Isolated Testing Mode

Use the isolated bootstrap when you explicitly want a disposable test
environment separate from the normal global store. For the primary user path,
prefer the `btwin test-env` CLI. `btwin test-env up` prepares the repo-scoped
test root at `.btwin-test-env/`, prepares the test project root at
`.btwin-test-env/project`, and starts or reuses only the test environment's
owned attached `serve-api`. It prints the exact `cd` command to launch Codex
from the test project root:

### Quick Start

Run these commands from this repository root:

```bash
btwin test-env up
btwin test-env status
cd .btwin-test-env/project
codex
```

The test project root is where you should run Codex, not this repository root.
`btwin test-env up` also prepares test-local Codex assets there, including:

- `.codex/config.toml`
- `.codex/hooks.json`
- a test-env-specific `AGENTS.md`

The repository's own `AGENTS.md` is left unchanged.

Quick verification after `btwin test-env up`:

```bash
btwin test-env status
ls .btwin-test-env/project/.codex
sed -n '1,40p' .btwin-test-env/project/AGENTS.md
```

You should see:

- the isolated root at `.btwin-test-env/`
- the test project root at `.btwin-test-env/project`
- `API health: ok`
- `.codex/config.toml`
- `.codex/hooks.json`
- a test-env-specific `AGENTS.md`

When you are done testing:

```bash
cd /path/to/btwin-runtime
btwin test-env down
```

### Optional HUD

If you want to monitor the test thread from a second terminal, open the HUD
separately:

```bash
cd /path/to/btwin-runtime
btwin test-env hud
```

`btwin test-env status` is the quickest way to confirm which isolated root,
project root, config path, data dir, and API URL are active for the test
environment. `btwin test-env down` stops only the test env's owned `serve-api`
and leaves the normal global `~/.btwin` service untouched.

### Legacy Shell Helper Fallback

If you still need the generated shell-scoped helpers, the older flow remains
available for fallback use:

```bash
./scripts/bootstrap_isolated_attached_env.sh start --skip-server \
  --root .btwin-attached-test \
  --project-root /tmp/btwin-workflow-constraints-project \
  --project btwin-workflow-constraints \
  --port 8788

source .btwin-attached-test/env.sh
btwin_test_up
btwin_test_hud
```

After you source `env.sh`, the generated test helpers stay scoped to that shell
and use only the isolated attached environment:

```bash
btwin_test_status
btwin_test_up
btwin_test_hud --thread <thread_id>
btwin_test_down
```

Plain `btwin` commands in that same sourced shell also use the isolated
environment:

```bash
btwin hud
btwin runtime current --json
```

When you need to launch Codex in this legacy flow, `cd` into the test project
root first and then start `codex` from there.

This is useful for:

- split-repo smoke tests
- temporary sandbox runs
- experiments that should not touch `~/.btwin`

The activation is shell-local only. When you use this isolated mode, remember:

- `BTWIN_CONFIG_PATH` and `BTWIN_DATA_DIR` should usually point at the same local root
- `btwin_test_up`, `btwin_test_hud`, `btwin_test_status`, and `btwin_test_down` only affect the sourced isolated env
- many `btwin` commands will keep reading and writing that repo-local store while those paths stay active
- shells that do not source `env.sh` continue to use the global `~/.btwin` default
- the repo-local `.btwin/` directory is local runtime state and should be ignored by git

For a repeatable attached-helper smoke that exercises the isolated bootstrap,
attached API-backed helper commands, runtime binding, and `protocol apply-next`
without touching your primary `~/.btwin`, run:

```bash
./scripts/attached_helper_smoke.sh
```

The script creates a fresh temp project root, starts the isolated attached API,
binds a thread, advances the protocol through the shared API path, clears the
runtime binding, and finishes by checking the attached `agent inbox --json`
surface.

For unified local test execution with per-run HTML reports and retained
artifacts, use:

```bash
uv run python scripts/run_tests.py unit
uv run python scripts/run_tests.py integration
uv run python scripts/run_tests.py cli-smoke
uv run python scripts/run_tests.py provider-smoke
```

The runner writes each execution under `.test-artifacts/<timestamp-group>/`,
updates `.test-artifacts/latest` to the newest run, and keeps the most recent
30 runs by default. Each run directory includes `report.html`, `metadata.json`,
and captured pytest stdout/stderr. Provider smoke is intentionally opt-in and
uses the default profile `app-server` long-term with `gpt-5.4-mini`.

`provider-smoke` is a group, not one monolithic test. Keep the group composed
of a shared baseline flow plus narrower gate/regression scenarios, then select
just the scenario you want when needed:

```bash
uv run python scripts/run_tests.py provider-smoke --pytest-arg tests/test_provider_smoke_runner.py::test_provider_smoke_runs_scripted_thread_flow
```

Provider smoke keeps btwin data/config isolated, but by default reuses the
current user's provider authentication home so attached Codex sessions can
actually authenticate. Override only the provider auth home with
`BTWIN_PROVIDER_AUTH_HOME` when you need a different authenticated profile.

For workflow-constraints validation in the preferred `btwin test-env` flow,
keep a second terminal open with:

```bash
btwin test-env hud --thread <thread_id>
```

If you are using the legacy `env.sh` fallback instead, keep a second terminal
open after you have already sourced `env.sh` in that shell so it is pointed at
the isolated attached environment, with either:

```bash
btwin hud --thread <thread_id>
```

or:

```bash
btwin thread watch <thread_id> --follow
```

Use `thread watch` when you want the canonical workflow event feed for one
thread, and use `hud` when you want the thread feed alongside the broader
runtime dashboard. `btwin runtime current --json` is still the source of truth
for binding state, especially because deterministic stale cleanup is currently
triggered by command paths such as `runtime current` rather than by every
observation surface.

## Repository Layout

```text
packages/
  btwin-core/   Core runtime/domain implementation
  btwin-cli/    CLI, HTTP API, MCP proxy, bundled runtime docs/skills
```

## Current Scope

This repository is intended to become the standalone runtime source of truth.

What is ready here:

- packaged runtime code
- package-owned bundled runtime assets
- CLI / API / MCP proxy code
- split-repo packaging work
- default runtime behavior based on `~/.btwin`

What still needs validation:

- clean wheel/venv smoke tests after split
- one-command bootstrap parity with the older integrated install flow
- end-to-end first-user onboarding clarity across local vs global install paths
