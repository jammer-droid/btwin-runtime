# btwin-core

Core domain package for B-TWIN.

`btwin-core` provides the local durable-memory and collaboration primitives that sit underneath the higher-level B-TWIN surfaces.

It includes:

- record and conversation storage
- semantic search and vector indexing
- session state
- orchestration record models
- the `BTwin` facade used by other B-TWIN runtimes

It does **not** include:

- MCP server entrypoints
- skill installation
- client setup for Codex, Claude Code, or Gemini
- dashboard or web runtime

Those belong to separate layers such as `btwin-cli`, `btwin-mcp`, and repo-level skill assets.

## Install

```bash
pip install btwin-core
```

Python 3.11+ is required.

Runtime dependencies are declared in the package and include:

- `chromadb`
- `litellm`
- `pydantic`
- `pyyaml`

## Quick Start

```python
from pathlib import Path

from btwin_core import BTwin, BTwinConfig

config = BTwinConfig(data_dir=Path(".btwin"))
twin = BTwin(config)

saved = twin.record(
    "Studied the current package split design.",
    topic="package-split",
    tldr="Saved a note about the current package split design.",
)

results = twin.search("package split", n_results=3)

print(saved["path"])
print(results[0]["content"])
```

## Main API Surface

### `BTwinConfig`

Configuration model for the core runtime.

Important field:

- `data_dir`: root directory used for durable B-TWIN data

If you do not set `data_dir`, the default resolution order is:

1. `BTWIN_DATA_DIR`
2. `./.btwin`
3. `~/.btwin`

### `BTwin`

High-level facade over storage, indexing, session handling, and search.

Common operations:

- `start_session(...)`
- `end_session(...)`
- `record(...)`
- `record_convo(...)`
- `search(...)`
- `import_entry(...)`

### Lower-Level Modules

You can also work with lower-level pieces directly:

- `btwin_core.storage`
- `btwin_core.indexer`
- `btwin_core.vector`
- `btwin_core.session`
- `btwin_core.orchestration_models`

## Data Directory Contract

`btwin-core` is a local-first package. It expects a writable B-TWIN data directory.

Common files and directories created under `data_dir` include:

- `entries/entry/...`
- `entries/convo/...`
- `entries/<project>/collab/...`
- `index/`
- `index_manifest.yaml`
- `summary.md`
- `settings/locale.json`

This on-disk layout is currently part of the package's practical runtime contract.

## Relationship To MCP And Skills

`btwin-core` is the library layer only.

If you want:

- MCP tools for Codex, Claude Code, or Gemini CLI
- skill installation such as `bt:save` or `bt:handoff`
- client config bootstrap

use the higher-level B-TWIN repo/runtime layer that packages:

- `btwin-core`
- MCP server/proxy code
- bundled skills
- setup/install commands

## Current Status

`btwin-core` is usable as a standalone exported package and has been verified with:

- wheel build from `packages/btwin-core`
- clean virtualenv install outside the monorepo
- standalone smoke flow for record, search, and orchestration record storage

Current caveats:

- the package still assumes the external `.btwin`-style data layout
- MCP, hooks, and skills are not bundled in this package

## Repository Context

In the development monorepo, `btwin-core` is maintained alongside higher-level runtime layers such as:

- MCP server and proxy code
- bundled B-TWIN skills
- setup and installation commands
- optional dashboard and web tooling

When exporting `btwin-core` as a standalone package or subtree, those higher-level runtime assets remain separate from this library package.
