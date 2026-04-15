# btwin-runtime

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

Install and verify the repo-local environment:

```bash
git clone https://github.com/jammer-droid/btwin-runtime.git
cd btwin-runtime
uv sync
uv run btwin --help
```

Install `btwin` as a normal CLI so Codex and launchd can call it directly:

```bash
cd btwin-runtime
uv tool install -e .
btwin --help
```

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

Install the bundled skills and then restart Codex so it reconnects with the new MCP config:

```bash
btwin install-skills --platform codex
```

After that, the normal daily workflow is:

1. keep `btwin serve-api` running in the background through launchd
2. let Codex connect via `btwin mcp-proxy`
3. use the global `~/.btwin` data directory

You should not need to run `btwin serve-api` manually in a terminal for normal use.

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
cd btwin-runtime
uv run btwin mcp-proxy
```

For a quick API health check:

```bash
curl -s http://localhost:8787/api/sessions/status
```

This flow is mainly for local development, smoke tests, and debugging from the
repo clone. It does not make `btwin` globally available to your shell or MCP client.

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

For Codex, `btwin init` writes the equivalent MCP entry automatically, and
`btwin install-skills --platform codex` installs the bundled skills.

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
test project root at `.btwin-test-env/project` and prints the exact `cd`
command to launch Codex there:

```bash
btwin test-env up
btwin test-env hud
```

Run Codex from that test project root, not from this repository root. The
repo's `AGENTS.md` is left unchanged.

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

For workflow-constraints validation, keep a second terminal open after you
have already sourced `env.sh` in that shell so it is pointed at the isolated
attached environment, with either:

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
