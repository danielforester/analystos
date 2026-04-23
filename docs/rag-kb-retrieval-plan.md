# RAG-Based KB Retrieval for AnalystOS

## Context

The current KB retrieval is entirely path-deterministic: skills construct file paths from known table/schema names and read those files directly. This works well for small KBs but breaks down in two ways the user identified:

1. **Session context overload** — at 100+ tables, the session-startup KB load (step 2 in `starter-project/CLAUDE.md`) reads too many files, bloating context and slowing every session.
2. **Entity discovery** — when an analyst doesn't know the table name, no existing skill can find `customer_metrics` from the query "where is customer LTV stored?"

This plan adds a **two-tier semantic index** alongside the existing path-lookup system. Path lookup is never removed; it remains the fast path. The index is the fallback and the session primer.

---

## Architecture: Two-Tier Index

```
.claude/kb-index/
  kb_fts.db        ← SQLite FTS5 keyword index; small (~1–2 MB); optionally committed to git
  kb_vectors.db    ← Embedding vectors; gitignored; local per-analyst; optional upgrade
```

**Why two separate files:**
- The FTS index is deterministic and tiny — safe to commit so teams share it via `git pull`, no rebuild required.
- The vector index depends on an embedding model/API and is non-deterministic across providers — must stay gitignored and be rebuilt locally.
- A team can start with FTS-only (zero new deps, commitable), then any analyst can layer on vectors locally without affecting teammates.

---

## Configuration (new `rag:` stanza in `active.yaml`)

The `rag:` block is top-level (not per-connection) because the index spans all connections in a KB.

```yaml
# ── Semantic KB search (optional) ────────────────────────────────────────────
# Remove this block entirely to disable RAG — all skills fall back to path lookup.
rag:
  enabled: true

  fts:
    index_path: .claude/kb-index/kb_fts.db
    commit_to_git: false   # set true to commit the FTS index for instant team sharing on git pull

  embeddings:
    enabled: false         # opt-in; FTS alone covers most queries
    provider: openai       # openai | anthropic | local | none
    model: text-embedding-3-small          # for openai
    api_key_env: OPENAI_API_KEY            # env var — never store the key value here
    local_model: all-MiniLM-L6-v2         # only used when provider: local
    index_path: .claude/kb-index/kb_vectors.db

  chunking:
    strategy: section      # section-level: each ## heading becomes its own indexed chunk
    max_chunk_tokens: 512

  search:
    default_top_k: 5
    fts_weight: 0.6        # weight in reciprocal rank fusion when both indexes present
    embedding_weight: 0.4

  session:
    prime_on_startup: true   # replace full KB file scan at session start with a compact index summary
    prime_top_k: 15          # number of top-ranked chunks to load into session context
```

**Embedding provider options:**

| `provider` | Requires | Notes |
|---|---|---|
| `openai` | `OPENAI_API_KEY` env var | ~$0.01–0.02 to index 200-table KB once |
| `anthropic` | `ANTHROPIC_API_KEY` env var | Uses Voyage embeddings via Anthropic endpoint |
| `local` | `pip install sentence-transformers` (~700MB with torch) | Fully offline; no API calls ever |
| `none` | Nothing | FTS-only mode; embeddings block is still parsed but skipped |

`kb_search.py` detects missing packages/keys at runtime and auto-degrades: `local → openai → none (FTS-only)`.

---

## New Files

### 1. `starter-project/.claude/scripts/kb_search.py`

The unified CLI. ~400 lines. Uses only stdlib (`sqlite3`, `json`, `pathlib`, `re`, `urllib.request`) plus optional `sentence-transformers` or `openai` for the vector path.

**CLI interface:**

```bash
# Build or rebuild index (both tiers based on config)
python .claude/scripts/kb_search.py --build [--kb db-knowledge/] [--incremental]

# Search
python .claude/scripts/kb_search.py --query "timestamp gotchas" [--top-k 5] [--format json|text]
python .claude/scripts/kb_search.py --query "customer LTV" --type table  # filter by doc_type

# Session prime (used by CLAUDE.md startup)
python .claude/scripts/kb_search.py --session-prime [--top-k 15] [--format context]

# Index health check (used by /db-status)
python .claude/scripts/kb_search.py --status [--format json]

# Config override
python .claude/scripts/kb_search.py --config .claude/db-connections/active.yaml
```

**Section-level chunking rules (applied during `--build`):**

| Source file | Chunks produced |
|---|---|
| `{table}.md` | One chunk per `## Section` heading (Purpose, Grain, Key Fields, Relationships, Gotchas, Sample Query) |
| `_gotchas.md` | One chunk per `### ⚠️ ...` entry block |
| `_schema-overview.md` | One chunk per entity cluster (Section 2) + one per relationship block (Section 3) + Section 6 (gotchas) as one chunk |
| `{query}.sql` | Comment header block as one chunk; SQL body as a second chunk |

Each chunk carries metadata: `{path, connection, schema, table, section, doc_type, last_modified, title}`.

**Incremental build:** Tracks file mtime in a `kb_files` table. On `--incremental`, only re-indexes files newer than their stored mtime. The hook uses this for near-instant updates after a single-file write.

**Search output (JSON):**

```json
{
  "query": "timestamp gotchas",
  "index_age_hours": 1.2,
  "results": [
    {
      "rank": 1,
      "score": 0.89,
      "path": "db-knowledge/oracle-prod/SALES/ORDERS.md",
      "connection": "oracle-prod",
      "schema": "SALES",
      "table": "ORDERS",
      "section": "Gotchas",
      "doc_type": "table",
      "title": "ORDERS — Gotchas",
      "excerpt": "⚠️ Timestamps store UTC but display in local time — cast with AT TIME ZONE before date math."
    }
  ],
  "error": null,
  "fallback": false
}
```

**Session prime output (`--format context`):**

A compact markdown block (not raw JSON) formatted for direct injection into Claude's session context. Contains: index health line, top-N most relevant chunks grouped by type (gotchas, tables with open questions, recently documented), and a list of undocumented tables (in schema_scope but missing from KB). Replaces the full KB file-read loop in the session startup checklist.

**Failure modes:**

| Condition | Behavior |
|---|---|
| Index missing | Returns `{"error": "index_not_built", "fallback": true}`. Calling skill falls back to path lookup. |
| Embedding API down | FTS search proceeds; embedding scoring skipped. Output notes degraded mode. |
| `sentence-transformers` not installed | Auto-falls back to `openai`; if that key also missing, falls back to `none` (FTS-only). Prints one-line warning. |
| SQLite locked | WAL mode enabled. Writers use 5-second timeout; hook reindex is non-blocking (subprocess). |

### 2. `install/skills/db-search/SKILL.md`

Dual-purpose skill:
- **User-invocable** (`user-invocable: true`) as `/db-search "query"` — analyst ad-hoc search across the entire KB.
- **Internal reference** (also callable by other skills) for semantic fallback.

Behavior:
1. Read `active.yaml`, check `rag.enabled`. If false or index missing, tell analyst to run `/db-index` first.
2. Call `python .claude/scripts/kb_search.py --query "{query}" --top-k {N} --format json`.
3. Parse results. For each result, present: table/file path, section, excerpt, last documented date.
4. If a result is a table the analyst hasn't explicitly named, offer: `"'customer_metrics' looks relevant — run /db-explain customer_metrics to see more."`
5. `allowed-tools: [Bash, Read]`

### 3. `install/hooks/db-kb-reindex.py`

Post-`Write` hook. Fires whenever Claude writes any file. Checks if the written path is inside `db-knowledge/`. If yes, runs `kb_search.py --build --incremental` as a non-blocking background subprocess. Respects `suppress_reindex_prompt: true` in `active.yaml`.

Pattern mirrors `db-capture-prompt.py`: reads stdin JSON, loads `active.yaml` via PyYAML with ImportError fallback, returns `{"action": "allow"}` (never blocks).

### 4. `starter-project/.claude/scripts/requirements-rag.txt`

```
# Optional: install for local (offline) embeddings. Pulls in PyTorch (~700MB CPU-only).
sentence-transformers>=2.6

# Optional: install for OpenAI embeddings (smaller alternative to sentence-transformers).
# The stdlib urllib.request path covers OpenAI API calls without this package,
# but this adds connection pooling and retry logic.
# openai>=1.0
```

---

## Changes to Existing Files

### `install/skills/db-index/SKILL.md`
Add **Step 6** after the existing Step 5 (Write the Updated README):

> **Step 6: Rebuild Semantic Index (if RAG enabled)**
>
> After confirming the README update, check `rag.enabled` in `active.yaml`.
> If true, run:
> ```
> python .claude/scripts/kb_search.py --build --kb {kb_path}
> ```
> Show the one-line output to the analyst (e.g., `KB semantic index updated: 312 chunks from 47 files.`).
> If `rag.enabled` is false or `active.yaml` has no `rag:` block, skip silently.

### `install/skills/db-gotchas/SKILL.md`
Add **Step 2e** after the existing four KB checks (2a–2d):

> **2e. Semantic fallback (if RAG enabled and steps 2a–2d found nothing)**
>
> If no relevant entries were found in 2a–2d, and `rag.enabled` is true in `active.yaml`, run:
> ```
> python .claude/scripts/kb_search.py --query "{target}" --top-k 5 --format json
> ```
> Present any results with score ≥ 0.1 under a `### Related KB entries (semantic match)` heading.
> These supplement the live fallback in Step 4 — do not skip live introspection if semantic results are inconclusive.

### `install/skills/db-query/SKILL.md`
Add to **Step 2** (KB pre-flight), before the existing path-based checks:

> If the analyst's question does not name a specific table AND `rag.enabled` is true, run:
> ```
> python .claude/scripts/kb_search.py --query "{analyst_question}" --top-k 3 --type table --format json
> ```
> Use the returned file paths as hints for which table docs and gotchas to read in 2b–2d.
> If the top result is a table not otherwise mentioned, note it: `"'customer_metrics' may be relevant based on KB search."`

### `install/skills/db-status/SKILL.md`
Add to the **KB inventory output section**:

> If `rag.enabled` is true in `active.yaml`, add a `Semantic Index` line to the status output:
> - Run `python .claude/scripts/kb_search.py --status --format json` and show:
>   `Semantic index: {N} chunks from {M} files, built {YYYY-MM-DD HH:MM} ({age})`
> - If index is missing: `Semantic index: not built — run /db-index to build`
> - If `rag.enabled` is false or no `rag:` block: omit the line entirely.

### `starter-project/CLAUDE.md` — Session Startup Step 2
Replace the existing step 2 with a RAG-aware version:

> **2. Load the knowledge base** — Scan `db-knowledge/`:
>
> **2a. Check RAG config.** Read `active.yaml`. If `rag.enabled: true` and `rag.session.prime_on_startup: true`:
> - Run `python .claude/scripts/kb_search.py --session-prime --top-k {prime_top_k} --format context`
> - If this succeeds: load the returned compact context block as your KB summary. **Skip steps 2b–2e** (the full file reads are replaced by the index summary). Proceed to step 2f.
> - If this returns `error: index_not_built`: fall through to 2b–2e (standard path), and note once to the analyst at greeting: `"Semantic index not found — run /db-index to build it for faster session loading."`
>
> **2b–2e.** (Existing steps — only reached if RAG is disabled or index is missing)
> - Read `README.md` for the connection/schema index.
> - Read `_gotchas.md` for cross-connection project-wide warnings.
> - Read `_open-questions.md` for unresolved issues.
> - Look for `db-knowledge/{connection-name}/` subfolder; if found, read `_gotchas.md` and `_schema-overview.md` for schemas in `schema_scope`.
>
> **2f. Keep KB context** — whether loaded via index (2a) or file reads (2b–2e), keep this in working context for the session.

### `install/settings.json`
Add to the `PostToolUse` hooks array:

```json
{
  "_comment": "KB reindex — incrementally rebuilds semantic index after any write to db-knowledge/",
  "matcher": "Write",
  "hooks": [{"type": "command", "command": "python ~/.claude/hooks/db-kb-reindex.py"}]
}
```

### `starter-project/.gitignore`
Add a new section with comments explaining the two-tier choice:

```gitignore
# ── Semantic KB index ────────────────────────────────────────────────────────
# kb_vectors.db  — embedding vectors; non-deterministic across providers; always gitignore
.claude/kb-index/kb_vectors.db

# kb_fts.db      — FTS keyword index; deterministic; small (~1-2MB for 200 tables)
# To share the FTS index with your team (instant search on git pull), comment out this line
# and set rag.fts.commit_to_git: true in active.yaml
.claude/kb-index/kb_fts.db

# Keep the directory itself untracked when both files are ignored:
# (git does not track empty directories)
```

### `starter-project/.claude/db-connections/connections.example.yaml`
Add the `rag:` stanza as a new commented section at the bottom, following the existing format of other optional stanzas (like `cost_thresholds`).

---

## What Stays Unchanged

- All existing path-deterministic lookups in every skill are **preserved**. RAG is always additive.
- The `db-safety`, `db-cost-gate`, `db-capture-prompt`, `db-doc-prompt` hooks are untouched.
- Every other existing skill (`db-explain`, `db-profile`, `db-joins`, `db-orient`, `db-document`, `db-capture`, `db-use`) is untouched in Phase 1. They benefit from session priming automatically without code changes.

---

## Phased Delivery

**Phase 1 (this plan):** Everything above — `kb_search.py`, `db-search` skill, `db-kb-reindex` hook, modifications to 5 skills and 4 config files. Delivers session priming + entity discovery + cross-KB gotcha search.

**Phase 2 (later, out of scope now):** Extend `db-explain` and `db-orient` to use `db-search` for "did you mean?" disambiguation. Add a `--session-prime` smart-sort that weights recent work and tables with open questions more heavily.

---

## Verification

1. **FTS build and search:**
   ```bash
   cd starter-project
   python .claude/scripts/kb_search.py --build --kb db-knowledge/
   python .claude/scripts/kb_search.py --query "soft delete" --format text
   # Expected: results from any _gotchas.md or table.md Gotchas sections
   ```

2. **Incremental build (hook simulation):**
   - Write a new table entry to `db-knowledge/demo/main/orders.md`
   - Run `python .claude/scripts/kb_search.py --build --incremental`
   - Verify only the new file is re-indexed (check stdout chunk count)

3. **Session prime:**
   ```bash
   python .claude/scripts/kb_search.py --session-prime --top-k 10 --format context
   # Expected: compact markdown block with gotchas, open questions, table summaries
   ```

4. **Status check via `/db-status`:**
   - Verify the "Semantic index:" line appears in output with correct chunk count and build time

5. **Graceful degradation:**
   - Remove `.claude/kb-index/kb_fts.db`, run `/db-explain orders`
   - Expected: skill proceeds with path lookup, no crash; session startup notes index missing once

6. **Connector tests (unchanged):**
   ```bash
   cd starter-project/.claude/scripts
   python -m pytest connectors/tests/
   ```

---

## Critical Files

| File | Change type |
|---|---|
| `starter-project/.claude/scripts/kb_search.py` | **New** — ~400 lines |
| `install/skills/db-search/SKILL.md` | **New** |
| `install/hooks/db-kb-reindex.py` | **New** |
| `starter-project/.claude/scripts/requirements-rag.txt` | **New** |
| `install/skills/db-index/SKILL.md` | Add Step 6 |
| `install/skills/db-gotchas/SKILL.md` | Add Step 2e |
| `install/skills/db-query/SKILL.md` | Add to Step 2 |
| `install/skills/db-status/SKILL.md` | Add index health line |
| `starter-project/CLAUDE.md` | Replace session startup Step 2 |
| `install/settings.json` | Register db-kb-reindex hook |
| `starter-project/.gitignore` | Add kb-index entries with comments |
| `starter-project/.claude/db-connections/connections.example.yaml` | Add `rag:` stanza |

**Reuse from existing codebase:**
- `starter-project/.claude/scripts/kb-to-obsidian.py` — `rglob` traversal pattern, frontmatter regex, section extraction logic: adapt directly into `kb_search.py`'s indexer
- `install/hooks/db-capture-prompt.py` — hook skeleton (stdin JSON parsing, YAML loading with ImportError fallback, `{"action": "allow"}` return): adapt directly for `db-kb-reindex.py`
- `starter-project/.claude/db-connections/active.yaml` — `rag:` stanza format mirrors existing `cost_thresholds:` pattern
