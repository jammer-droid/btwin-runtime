# B-TWIN Usage Guidelines

> These guidelines must be followed by all LLM agents using btwin MCP tools.
> Call `btwin_get_guidelines` at the start of every session to load these rules.
> For orchestration (workflows, agents), use Skills (`/bt:*` commands) — see `global/orchestration-guidelines.md`.

## Recommended Agent Bootstrap Order

1. `btwin_get_guidelines`
2. `btwin_search` when prior context is needed
3. Use `/bt:*` Skills as needed (see README Skills table for full list)

## Runtime Modes

- **attached** (default): shared mode. `serve-api` is the memory/index owner, and stdio entrypoints such as `btwin serve` / `btwin mcp-proxy` use that shared backend.
- **standalone**: local-only mode. Direct CLI/local runtime paths resolve storage from the current process and support `btwin chat`.
- `btwin chat` is standalone-only because it depends on a direct local LLM/session loop rather than the shared API path.

## Recording Rules

### Always use absolute paths
- When recording file paths, **always use absolute paths**.
- Relative paths cannot be resolved when searching from other projects.
- Good: `/home/user/projects/my-app/docs/plans/roadmap.md`
- Bad: `docs/plans/roadmap.md`

### Conversation recording
- **Session management**: Open a topic session with `btwin_start_session` at the start of a conversation, save a summary with `btwin_end_session` when done.
- **Interim recording**: Record important content immediately with `btwin_convo_record`.
- **General notes**: Save the user's thoughts/notes with `btwin_record`.
- **Skill shortcut**: Use `/bt:save` when the user wants "save this conversation/progress" and you need a consistent tool-selection workflow.
- **Handoff shortcut**: Use `/bt:handoff` when the goal is to help the next worker resume in a fresh session with background, progress, and next steps.
- **Skill refresh shortcut**: Use `/bt:sync-skills` when bundled bt skills changed in the repo and the current platform install needs to be updated.
- **TLDR mandatory**: All record tools (`btwin_record`, `btwin_convo_record`, `btwin_end_session`) require the `tldr` parameter.

### Autonomous recording criteria
Record autonomously even without explicit user request when:
- Career direction decisions or changes
- Study/learning plan creation or revision
- Important decisions
- User expresses strong opinions or values
- New interests or goals are revealed

### Agent registration
- For workflow/orchestration sessions, register with `/bt:register` before using workflow commands.
- See `global/orchestration-guidelines.md` for full registration rules and agent-workflow behavior.

### Date format
- Use absolute dates (e.g., 2026-03-12)
- Relative dates ("today", "tomorrow", "next week") lose meaning over time

## Standard Frontmatter Schema

All entries include YAML frontmatter. Fields are categorized by who creates them.

### Auto-generated fields (system creates on save)

| Field | Type | Description |
|-------|------|-------------|
| `record_id` | string | Unique ID: `{record_type}-{HHMMSSffffff}{3digits}` |
| `record_type` | string | `convo`, `entry`, `note` |
| `date` | string | `YYYY-MM-DD` |
| `created_at` | string | ISO 8601 |
| `last_updated_at` | string | ISO 8601 |
| `source_project` | string | Auto-detected from git remote or CWD |
| `contributors` | list[string] | Contributor identifiers stored on the record. Defaults to `unknown` unless a caller supplies contributor data |

### Required LLM-written fields

| Field | Type | Description |
|-------|------|-------------|
| `tldr` | string | Search summary, 1-3 sentences, max 200 chars |

### Optional LLM-written fields

| Field | Type | Description |
|-------|------|-------------|
| `tags` | list[string] | Topic tags |
| `subject_projects` | list[string] | Projects this entry relates to |

### Relationship fields (added via `btwin_update_entry`, empty at creation)

| Field | Type | Description |
|-------|------|-------------|
| `derived_from` | string | Parent entry's record_id |
| `related_records` | list[string] | Related entry record_ids |

### Import-only fields

| Field | Type | Description |
|-------|------|-------------|
| `imported_at` | string | ISO 8601 import timestamp |
| `source_path` | string | Original file path before import |

## TLDR Writing Rules

Every entry must include a `tldr` field. Follow these rules:

| Rule | Description |
|------|-------------|
| **Length** | 1-3 sentences, max 200 chars |
| **Content** | Concrete facts and decisions only. No vague descriptions |
| **Keywords** | Include searchable terms an LLM would query for |
| **Language** | Match the original content language |

**Good examples:**
- `"Finalized TLDR-based indexing design. ChromaDB stores TLDR only, full content via MCP Resource btwin://record/{id}."`
- `"Decided on ChromaDB for vector search. SQLite for metadata, file-based storage for content."`

**Bad examples:**
- `"Discussed various topics about the project."` (too vague, no search keywords)
- `"Project-related conversation."` (no specifics)

## Record Types

| Type | Use |
|------|-----|
| `convo` | Conversation session summaries |
| `entry` | General records, analyses, notes |
| `note` | Short memos |

## Storage Structure

All entries follow a unified path based on record type and date:

```
entries/{record_type}/{date}/{record_id}.md
```

Examples:
- `entries/convo/2026-03-12/convo-074050723201.md`
- `entries/entry/2026-03-12/entry-074500123456.md`
- `entries/note/2026-03-12/note-120000000001.md`

Project info is stored in **frontmatter (`source_project`) only**, NOT in the file path.

## How Records Are Stored

| Tool | Storage location | Record type |
|------|-----------------|-------------|
| `btwin_record` | `entries/entry/{date}/{record_id}.md` | `entry` |
| `btwin_convo_record` | `entries/convo/{date}/{record_id}.md` | `convo` |
| `btwin_end_session` | `entries/convo/{date}/{record_id}.md` | `convo` |
| `btwin_import_entry` | `entries/entry/{date}/{record_id}.md` | `entry` |

## Search → Full Content Workflow

1. `btwin_search(query)` → vector similarity search on TLDR → returns record_id, tldr, metadata
2. If full content is needed → MCP Resource `btwin://record/{record_id}` to fetch full markdown
3. Skip full content if TLDR is sufficient (saves tokens)

## btwin_update_entry Tool

Update an existing entry by record_id.

```
btwin_update_entry(
  record_id,           # Required: ID of the record to update
  content?,            # Optional: modify body content
  tags?,               # Optional: update tags
  subject_projects?,   # Optional: update related projects
  related_records?,    # Optional: list of related record IDs
  derived_from?,       # Optional: parent record ID
  contributor?         # Optional explicit contributor identifier
)
```

- Auto-updates `last_updated_at`
- Appends `contributor` to `contributors` when the caller provides it
- Used for relationship linking, tag updates, and content changes

## btwin_record Response Shape

`btwin_record` returns a JSON-serialized response (string) with an `action` field. Parse it with `json.loads()` before inspecting.

### Three possible actions

| Action | Meaning | When it happens |
|--------|---------|-----------------|
| `created` | A new entry was saved | Similarity < `suggest_threshold` (default 0.80), or consolidation was disabled |
| `consolidated` | An existing entry was updated in place with the new content merged in | Similarity >= `auto_threshold` (default 0.95) |
| `created` with `similar_candidates` | A new entry was saved, but moderately similar entries exist | Similarity in `[suggest_threshold, auto_threshold)` |

### Response fields

Always present:
- `action` — `"created"` or `"consolidated"`
- `record_id` — the record that was written or updated
- `path`, `date`, `slug` — file location and identifiers

Present only when consolidated:
- `matched_score` — cosine similarity of the merged candidate (0.0–1.0)

Present only when moderately similar entries exist:
- `similar_candidates` — list of `{record_id, score, tldr, path}` dicts

### How agents should react

- **On `consolidated`**: Inform the user that the new content was merged into an existing entry (mention `record_id`). Do not re-record.
- **On `created` with `similar_candidates`**: Optionally mention the closest candidate to the user so they can decide whether to link records manually via `btwin_update_entry(related_records=[...])`. Do not auto-merge.
- **On plain `created`**: Proceed normally — a new entry was written.

Consolidation only applies to `record_type: entry`. `btwin_convo_record` never consolidates (conversation history is preserved verbatim).

### Configuring consolidation

Consolidation behavior is tunable in `~/.btwin/config.yaml` under the `consolidation:` key. All fields are optional — omit to use defaults.

```yaml
consolidation:
  enabled: true            # Set to false to bypass middleware entirely (default: true)
  auto_threshold: 0.95     # Similarity >= this → automatic merge (default: 0.95)
  suggest_threshold: 0.80  # Similarity >= this → create + surface candidates (default: 0.80)
  search_candidates: 3     # How many nearest neighbors to fetch per write (default: 3)
```

**Field meanings:**

| Field | Effect |
|-------|--------|
| `enabled` | Master switch. When `false`, `btwin_record` behaves exactly like before the feature — no similarity check, always creates a new entry. |
| `auto_threshold` | Cosine similarity (0.0–1.0) at or above which the new record is merged into the best-matching existing entry. Higher values = more conservative (fewer auto-merges). |
| `suggest_threshold` | Cosine similarity at or above which candidate matches are surfaced in the response even when no auto-merge happens. Must be ≤ `auto_threshold`. |
| `search_candidates` | Number of nearest neighbors `vector_store.search()` fetches per write. Usually 3 is fine; raise if you want the response to surface more suggestions. |

**Tuning tips:**
- If consolidation is merging records that shouldn't be merged → raise `auto_threshold` (e.g. to 0.97).
- If you want more suggestions but no auto-merges → raise `auto_threshold` to `1.01` (effectively off) and keep `suggest_threshold` at 0.80.
- To disable entirely: set `consolidation.enabled: false` or delete the file key.

**Applying changes:**

After editing `~/.btwin/config.yaml`, restart the server so the new config is loaded:

```bash
launchctl kickstart -k gui/$(id -u)/com.btwin.serve-api
```

The existing ChromaDB index does NOT need to be rebuilt for threshold changes — only `enabled`/thresholds are read at request time. The one-time cosine metric rebuild happens automatically on the first startup after the feature landed.

## Search Rules

### Search order
- When user references a past conversation → search with `record_type: "convo"` first
- When looking for documents/artifacts → search without filters

### Search usage
- Actively use `btwin_search` when past conversation context is needed
- Always verify with search when the user mentions a previous conversation
- Prefer MCP search first, then fetch full content via `btwin://record/{record_id}` only when the TLDR is insufficient

### Search scope
- `scope: "project"` (default for MCP tools) — searches current project only
- `scope: "all"` — searches across all projects
- CLI `btwin search` in attached mode always uses `scope: "all"` (cross-project search by default)

## Troubleshooting

### Server not responding

```bash
curl http://localhost:8787/api/sessions/status    # Check server response
launchctl print gui/$(id -u)/com.btwin.serve-api  # Check service status
cat ~/.btwin/logs/serve-api.stderr.log             # Check logs
```

If attached-mode `btwin search` or `btwin record` fails:
- restart `btwin serve-api`
- verify `BTWIN_API_URL` if you use a custom backend
- use `runtime.mode: standalone` only if you intentionally want a local-only runtime path

### btwin command not found

```bash
which btwin   # Should be ~/.local/bin/btwin
```

If `~/.local/bin` is not in PATH, add to your shell config:
```bash
export PATH="$HOME/.local/bin:$PATH"
```
