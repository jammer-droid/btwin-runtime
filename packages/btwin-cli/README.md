# btwin-cli

CLI control-plane package for the B-TWIN runtime.

`btwin-cli` is the package that turns `btwin-core` into a usable local runtime.
It provides:

- the `btwin` command-line interface
- the HTTP API served by `btwin serve-api`
- the stdio MCP proxy used by Codex and other MCP clients
- provider bootstrap such as `btwin init`
- bundled runtime docs, protocol definitions, and installable skills

It sits above `btwin-core` and depends on it for storage, indexing, thread
state, and runtime domain logic.

## Install

```bash
pip install btwin-cli
```

Python 3.11+ is required.

For development from this repository:

```bash
uv sync
uv run btwin --help
```

## Main Surfaces

### CLI

The `btwin` CLI is defined by the package entrypoint in `pyproject.toml`.

Common commands include:

- `btwin init`
- `btwin handoff --record-id ...`
- `btwin handoff list`
- `btwin handoff show`
- `btwin serve-api`
- `btwin mcp-proxy`
- `btwin service install`
- `btwin install-skills --platform codex`

### HTTP API

`btwin serve-api` starts the FastAPI application that acts as the shared runtime
backend for search, recording, settings, orchestration, and thread operations.

### MCP Proxy

`btwin mcp-proxy` exposes a lightweight stdio MCP server that forwards requests
to the HTTP API. This is the normal bridge used by Codex:

```text
Codex -> btwin mcp-proxy -> btwin serve-api -> ~/.btwin
```

## Provider Model

Today `btwin-cli` supports exactly one provider bootstrap path: Codex.

- `btwin init` validates that the `codex` CLI exists
- it creates `~/.btwin/providers.json`
- it writes the MCP client entry for `btwin mcp-proxy`

Additional providers may be added later, but the current packaged workflow is
intentionally Codex-first.

## Bundled Skills

`btwin-cli` ships the B-TWIN skills that can be installed into supported client
environments.

These skills are small workflow guides, not executable server features. They
help an agent choose the right B-TWIN tools or commands for recurring tasks.

Examples include:

- `bt:save`
- `bt:handoff`
- `bt:scenario-smoke`
- `bt:sync`
- `bt:list`
- `bt:status`
- `bt:update`

Install them with:

```bash
btwin install-skills --platform codex
```

After pulling repo changes, refresh the installed `btwin` executable first if the
update touched CLI/runtime packaging, then re-run `btwin install-skills`.

The package also bundles runtime docs and global protocol assets so the CLI can
sync them into the active B-TWIN data directory when needed.

## Relationship To btwin-core

Use `btwin-core` if you want the library layer only.

Use `btwin-cli` if you want:

- the `btwin` executable
- the HTTP API
- the MCP proxy
- provider initialization
- bundled skills and runtime docs

In this repository, those layers live side-by-side:

- [`../btwin-core/README.md`](../btwin-core/README.md)
- [`../../README.md`](../../README.md)
