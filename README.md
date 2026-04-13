# btwin-runtime

Runtime workspace for the packaged B-TWIN stack.

This repository keeps the runtime-facing packages together in one place:

- `packages/btwin-core`
- `packages/btwin-cli`

`btwin-core` owns the domain/runtime implementation.
`btwin-cli` provides the CLI, HTTP API, and MCP proxy surface on top of it.

The Codex provider implementation currently stays inside `btwin-core`, so there
is no separate provider package in this split.

## Start Here

If you just cloned this repository and want to use it immediately, start with
the local repo-driven workflow:

1. Install dependencies with `uv`
2. Run the CLI with `uv run btwin ...`
3. Use a repo-local data directory while testing

This repository is suitable for:
- packaged runtime development
- `btwin-core` / `btwin-cli` work
- `serve-api` and `mcp-proxy` development
- runtime packaging and split-repo validation

## Prerequisites

- Python 3.11+
- `uv`

Install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Quick Start

Clone the repo and install dependencies:

```bash
git clone https://github.com/jammer-droid/btwin-runtime.git
cd btwin-runtime
uv sync
```

Use a repo-local data directory while testing:

```bash
export BTWIN_DATA_DIR="$(pwd)/.btwin"
mkdir -p "$BTWIN_DATA_DIR"
```

Check the CLI entrypoint:

```bash
uv run btwin --help
```

Start the HTTP API:

```bash
uv run btwin serve-api
```

In another terminal, start the MCP proxy:

```bash
cd btwin-runtime
export BTWIN_DATA_DIR="$(pwd)/.btwin"
uv run btwin mcp-proxy --project demo
```

## Codex / MCP Setup

This repository already contains the packaged runtime assets needed by:
- `btwin mcp-proxy`
- `btwin serve-api`
- bundled `global/` docs
- bundled `skills/`

For local testing from this clone, prefer `uv run btwin ...` commands first.

If you want to install the CLI globally later, you can experiment with:

```bash
uv tool install -e .
```

After that, `btwin init --provider codex` and `btwin install-skills --platform codex`
can be tested against the globally installed command.

## Repository Layout

```text
packages/
  btwin-core/   Core runtime/domain implementation
  btwin-cli/    CLI, HTTP API, MCP proxy, bundled skills/docs
```

## Current Scope

This repository is intended to become the standalone runtime source of truth.

What is ready here:
- packaged runtime code
- package-owned bundled assets
- CLI / API / MCP proxy code
- split-repo packaging work

What still needs validation:
- clean wheel/venv smoke tests after split
- full standalone bootstrap flow parity with the older integrated setup path
