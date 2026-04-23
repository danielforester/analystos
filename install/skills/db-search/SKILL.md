---
name: db-search
description: Dual-purpose semantic KB search. User-invocable as /db-search "query" for ad-hoc full-KB search by concept or keyword (e.g. "where is customer LTV stored", "timestamp gotchas"). Also callable by other skills as a semantic fallback when path-based lookup finds nothing. Requires the semantic index — run /db-index to build it.
user-invocable: true
argument-hint: "<search query>"
allowed-tools:
  - Bash
  - Read
---

# /db-search

**Invocation:** `/db-search "<search query>"`

Use this command when you know what you're looking for conceptually but not which table or file documents it. Examples:

- `/db-search "where is customer lifetime value stored"` — finds `customer_metrics.md` even without knowing the table name
- `/db-search "timestamp gotchas"` — surfaces gotcha entries mentioning time zones or epoch conversions across all schemas
- `/db-search "soft delete"` — finds all KB entries documenting soft-delete patterns

This skill is also invoked internally by `db-gotchas` and `db-query` as a semantic fallback when their path-based KB lookups find nothing.

---

## Behavior Overview

1. Check RAG config and index availability
2. Run the semantic search
3. Present results with follow-up hints
4. (When called internally) Return results under a semantic-match heading

---

## Step 1: Check RAG Config

Read `.claude/db-connections/active.yaml`. Check `rag.enabled`.

If `rag.enabled` is explicitly `false` or the `rag:` block is absent:
> "Semantic search is not configured. Add a `rag:` block to `.claude/db-connections/active.yaml` and run `/db-index` to build the index."
Stop here.

If `rag.enabled` is true but `.claude/kb-index/kb_fts.db` does not exist:
> "Semantic index not found. Run `/db-index` to build it first."
Stop here.

---

## Step 2: Run the Semantic Search

Determine the search parameters from `active.yaml`:
- `top_k` = `rag.search.default_top_k` (default: 5)
- If the analyst passed `--type {type}`, add `--type {type}` to the command

Run:

```
python .claude/scripts/kb_search.py --query "{query}" --top-k {top_k} --format json
```

**Handling errors:**
- JSON contains `"error": "index_not_built"` → tell analyst to run `/db-index` first
- Command exits non-zero or returns malformed JSON → surface the raw stderr output; do not crash

---

## Step 3: Present Results

Format results as:

```
## KB Search: "{query}"
Index age: {age}h

1. **{title}**
   Path: `{path}`
   Type: {doc_type}  |  Section: {section}
   > {excerpt}

2. ...
```

**Discovery hint** — if a result contains a table the analyst has not explicitly named in the conversation, add:
> "`{table_name}` looks relevant — run `/db-explain {table_name}` to see the full entry."

**Weak-match note** — if the top score is below 0.1 (weak FTS match), add:
> "These are weak matches — the KB may not document this topic yet. Try `/db-orient {schema}` to explore the live database."

**No results:**
> "No KB entries found for `{query}`. Try `/db-orient {schema}` to introspect the live database."

---

## Step 4: Follow-up Actions

After results, offer relevant next steps (only those applicable to the returned types):

| Result doc_type | Suggested action |
|---|---|
| `table` | `/db-explain {table_name}` for the full data dictionary entry |
| `gotchas` | "These gotchas are already in your session context if the index was used at startup." |
| `query` | "View the full SQL in `{path}`." |
| `schema_overview` | `/db-orient {schema}` for interactive exploration |

---

## When Called Internally by Other Skills

When `db-search` is invoked as a fallback from `db-gotchas`, `db-query`, or another skill:

1. Run the search without preamble or explanation
2. Filter to results with score ≥ 0.1
3. Present under a `### Related KB entries (semantic match)` heading
4. Do not offer follow-up actions — the calling skill controls the flow

---

## Notes

- `/db-search` never modifies the KB — it is always read-only
- The index is built by `/db-index` and kept incrementally fresh by the `db-kb-reindex` post-write hook
- If the analyst asks "where is X stored?" or "which table has Y?", invoke this skill before introspecting the live database
- For best results, run `/db-index` after any significant documentation session
