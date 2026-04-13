# btwin-runtime

Packaged runtime workspace for B-TWIN.

This repository keeps the runtime-facing packages together in one place:

- `packages/btwin-core`
- `packages/btwin-cli`

`btwin-core` owns the domain/runtime implementation.
`btwin-cli` provides the CLI, HTTP API, and MCP proxy surface on top of it.

The Codex provider implementation currently stays inside `btwin-core`, so there
is no separate provider package in this split.

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
- project-local `.btwin/` is an exception for isolated testing, not the default

## Prerequisites

- Python 3.11+
- `uv`

Install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Minimal Setup

Clone the repo and install dependencies:

```bash
git clone https://github.com/jammer-droid/btwin-runtime.git
cd btwin-runtime
uv sync
```

Check the CLI entrypoint:

```bash
uv run btwin --help
```

At this point you can run individual CLI commands. This does not yet mean the
shared runtime is running.

## Recommended Runtime Setup

The normal local setup is:

1. use the global `~/.btwin` data directory
2. start `serve-api`
3. connect clients through `mcp-proxy`

Start the shared API:

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

## Codex / MCP Setup

This repository already contains the packaged runtime assets needed by:

- `btwin serve-api`
- `btwin mcp-proxy`
- bundled runtime docs and defaults
- bundled protocol definitions
- bundled skills

For local testing from this clone, prefer `uv run btwin ...` first.

If you want a globally available `btwin` command later, you can experiment with:

```bash
uv tool install -e .
```

Then configure your MCP client to run:

```text
command: btwin
args: ["mcp-proxy"]
```

For Codex, the equivalent config is:

```toml
[mcp_servers.btwin]
command = "btwin"
args = ["mcp-proxy"]
```

After global install, provider-specific bootstrap can be tested with commands
such as:

```bash
btwin init --provider codex
btwin install-skills --platform codex
```

## Isolated Testing Mode

Use a repo-local data directory only when you explicitly want isolation from the
normal global store:

```bash
export BTWIN_DATA_DIR="$(pwd)/.btwin"
mkdir -p "$BTWIN_DATA_DIR"
uv run btwin serve-api
```

This is useful for:

- split-repo smoke tests
- temporary sandbox runs
- experiments that should not touch `~/.btwin`

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
- background service registration helpers such as launchd setup
