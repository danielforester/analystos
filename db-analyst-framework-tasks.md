# DB Analyst Framework — Task Breakdown

**Version:** 0.1
**Last Updated:** 2026-04-06

---

## Sequencing Overview

```
Epic 1: Foundation & Configuration
    └── Epic 2: Safety & Guardrails
            ├── Epic 3: Discovery Commands
            │       └── Epic 5: Documentation Commands & Knowledge Base
            └── Epic 4: Work Commands
                    └── Epic 5: Documentation Commands & Knowledge Base
```

Epics 3 and 4 can be parallelized once Epic 2 is complete. Epic 5 requires
T3.1 (db-introspect skill) before T5.1 (db-doc-writer skill) can begin;
the remaining Epic 5 tasks follow from there.

---

## Epic 1 — Foundation & Configuration

> Everything else depends on this. Establishes the connection model, dialect routing,
> directory scaffold, and the CLAUDE.md context-loading pattern.

**Depends on:** Nothing
**Unlocks:** Epic 2

| ID | Task | Description | Notes |
|----|------|-------------|-------|
| T1.1 | Define `connections.yaml` schema | Fields for db type, host, credential references (env vars only — no plain-text secrets), cost thresholds, read-only flag, and active schema scope | Deliver `connections.example.yaml` + validation spec |
| T1.2 | Build `db-dialect` skill | Routing layer that returns the correct metadata queries, syntax patterns, and cost-signal approach given the active database type | Must cover Oracle, Snowflake, Athena, Salesforce from day one |
| T1.3 | Write `CLAUDE.md` template | Project-level Claude Code instructions: loads KB context at session start, sets read-only expectation, points to active connection profile | Include the `db-context-loader` session-start hook |
| T1.4 | Scaffold `db-knowledge/` directory | Create full directory structure with README index, `_gotchas.md`, `_open-questions.md`, `_external-docs/README.md`, and per-schema placeholders | Use `_schema-overview-template.md` as the shipped template |
| T1.5 | Write `.gitignore` and repo setup guide | Ensure `active.yaml`, `.env`, and any credential files are excluded; document Phase 1 (shared drive) and Phase 2 (git) setup steps | Include instructions for both Windows and Unix paths |

---

## Epic 2 — Safety & Guardrails

> Build this before any query-executing command. Safety hooks must underpin
> everything — they should never be bolted on afterward.

**Depends on:** Epic 1
**Unlocks:** Epics 3, 4

| ID | Task | Description | Notes |
|----|------|-------------|-------|
| T2.1 | Build `db-safety` pre-tool-use hook | Parses every query string for DDL (`CREATE`, `DROP`, `ALTER`, `TRUNCATE`) and DML (`INSERT`, `UPDATE`, `DELETE`, `MERGE`) before execution. Blocks and requires explicit override phrase if detected | Must handle multi-statement and CTE patterns, not just first token |
| T2.2 | Build `db-cost-check` skill | Per-dialect cost signal logic: `EXPLAIN PLAN` for Oracle, bytes-scanned parsing for Athena, heuristic checks (no WHERE on large tables) for Snowflake, governor-limit estimation for Salesforce | Returns structured output: `{approved: bool, warning: string, estimated_cost: string}` |
| T2.3 | Build `db-cost-gate` pre-tool-use hook | Integrates `db-cost-check`; surfaces estimate to analyst; requires confirmation above configurable threshold before proceeding | Thresholds read from `active.yaml` |
| T2.4 | Build Salesforce SOQL safety layer | API limit warnings, non-selective filter detection (non-indexed WHERE clauses), SOQL-specific read-only enforcement (block REST write calls) | Fold into T2.1 and T2.2 or implement as Salesforce-specific variants |

---

## Epic 3 — Discovery Commands

> The core analyst-facing value for new-to-database workflows.
> These commands produce the orientation and understanding that makes everything else faster.

**Depends on:** Epic 2
**Unlocks:** Epic 5 (T5.1 depends on T3.1)

| ID | Task | Description | Notes |
|----|------|-------------|-------|
| T3.1 | Build `db-introspect` skill | Dialect-aware metadata extraction: tables, columns, data types, comments, PKs, FKs, indexes, row counts where cheaply available | This is the most foundational skill — Epic 5 builds on it |
| T3.2 | Build `db-sample` skill | Safe, cost-controlled data sampling. Uses `LIMIT` / `FETCH FIRST`, avoids full scans. Adapts per dialect. Returns representative rows + basic value distribution | Sample size and cost ceiling configurable via `active.yaml` |
| T3.3 | Implement `/db-orient` command | Calls `db-introspect` + `db-sample`; synthesizes a structured orientation summary (entity clusters, major tables, notable relationships, any available comments). Offers to save result to `_schema-overview.md` | Primary entry point for new analysts |
| T3.4 | Implement `/db-profile` command | Table or column stats: row count, null rate, distinct values, min/max, top-N values, sample rows. Runs through `db-cost-gate` before execution | Support both table-level and column-level invocation |
| T3.5 | Implement `/db-explain` command | Plain-English explanation of a table, column, view, or query. Synthesizes schema metadata, column comments, and sample data. Offers to save to knowledge base | Should infer grain and likely purpose even without comments |
| T3.6 | Implement `/db-joins` command | Discovers join paths between two or more named tables. Checks cardinality via COUNT/COUNT(DISTINCT). Flags fan-out risk. Outputs in the structured relationship block format used in `_schema-overview.md` | See `_schema-overview-template.md` Section 3 for target format |
| T3.7 | Implement `/db-status` command | Shows: active connection, current schema scope, loaded KB entries, last introspection timestamp, cost threshold settings | Lightweight — no DB calls required |

---

## Epic 4 — Work Commands

> Query assistance for analysts who know what they want.
> Builds on the safety layer and the dialect skill.

**Depends on:** Epic 2
**Unlocks:** Epic 5 (parallel path)

| ID | Task | Description | Notes |
|----|------|-------------|-------|
| T4.1 | Implement `/db-query` command | Natural-language to SQL: interprets analyst intent, drafts query, runs through `db-safety` + `db-cost-gate`, executes, then calls `db-explain-result`. Loads relevant KB gotchas before drafting | The highest-value single command for day-to-day analyst work |
| T4.2 | Build `db-explain-result` skill | Post-execution result interpretation: given query + result set, synthesizes what the numbers suggest and flags anomalies (unexpected nulls, suspiciously round numbers, row counts that imply fan-out, etc.) | Should be usable standalone, not only via `/db-query` |
| T4.3 | Implement `/db-gotchas` command | Loads and surfaces all KB gotcha entries relevant to a named table or schema. Falls back to live introspection hints (e.g., detects soft-delete columns by convention) if no KB entry exists | Read-only KB operation — no DB calls unless KB is empty |
| T4.4 | Build `db-soql` skill | Salesforce-specific: SOQL construction, `describeSObject` relationship traversal, parent-child subquery patterns, API limit awareness, translation of relational concepts to Salesforce object model | Salesforce's object model is sufficiently different to warrant its own skill |

---

## Epic 5 — Documentation Commands & Knowledge Base

> The durability layer — where the value compounds over time.
> Every other epic produces findings; this one makes them permanent.

**Depends on:** T3.1 (`db-introspect`); remainder of Epic 3/4 can run in parallel
**Unlocks:** Nothing (terminal epic), but feeds back into all commands via KB context

| ID | Task | Description | Notes |
|----|------|-------------|-------|
| T5.1 | Build `db-doc-writer` skill | Drafts structured markdown from introspection output + analyst-provided context. Outputs to the per-table format defined in `{table}.md` template and the relationship block format in `_schema-overview.md` | Depends on T3.1; this is the writing engine for T5.2 and T5.3 |
| T5.2 | Implement `/db-document` command | Interactive table or field documentation flow. Runs introspection, presents draft to analyst, accepts corrections/additions, writes final entry to `db-knowledge/{schema}/{table}.md` | Should detect and offer to update an existing entry rather than overwrite silently |
| T5.3 | Implement `/db-capture` command | Saves the current query + analyst-provided context (purpose, result interpretation, caveats) as a named `.sql` file in `db-knowledge/{schema}/_queries/`. Prompts for name and description | Lightweight — no introspection required |
| T5.4 | Build `db-doc-prompt` post-tool-use hook | After a `/db-explain` or `/db-orient` session, offers to persist the result as a KB markdown entry. Should not prompt on every run — use a session flag to avoid repetition | User can suppress with a config flag in `active.yaml` |
| T5.5 | Build `db-capture-prompt` post-tool-use hook | After a successful query that appears significant (non-trivial result, execution time above threshold, or analyst adds a comment), offers to invoke `/db-capture` | Significance heuristic: configurable row-count floor + time floor |
| T5.6 | Build KB index updater | Keeps `db-knowledge/README.md` in sync when new table files or schema folders are added. Scans directory and regenerates the index table automatically | Can run as a post-write hook or as a standalone `/db-index` command |
| T5.7 | Build `kb-to-obsidian.py` conversion script | **Opt-in.** Converts standard markdown KB files to an Obsidian-compatible vault: rewrites relative markdown links to `[[wikilinks]]`, injects YAML frontmatter from parsed file headers, and optionally converts `⚠️` warning patterns to Obsidian callout syntax. Writes to a separate `_obsidian-vault/` output directory — never modifies source files. Supports `--watch` flag for live sync during a Claude Code session. | Output directory is gitignored. Flags: `--source`, `--output`, `--frontmatter`, `--callouts`, `--watch`. Good first external contribution candidate — no DB access required. |

---

## Dependency Graph (Full)

| Task | Depends On |
|------|------------|
| T1.1–T1.5 | — |
| T2.1–T2.4 | T1.1, T1.2 |
| T3.1 | T2.1, T2.3, T1.2 |
| T3.2 | T2.3, T1.2 |
| T3.3 | T3.1, T3.2 |
| T3.4 | T3.2, T2.3 |
| T3.5 | T3.1, T3.2 |
| T3.6 | T3.1, T2.3 |
| T3.7 | T1.3 |
| T4.1 | T2.1, T2.3, T1.2 |
| T4.2 | — (standalone skill) |
| T4.3 | T1.4 |
| T4.4 | T2.1, T2.4, T1.2 |
| T5.1 | T3.1 |
| T5.2 | T5.1, T3.1 |
| T5.3 | T1.4 |
| T5.4 | T3.5, T3.3 |
| T5.5 | T2.3 |
| T5.6 | T1.4 |
| T5.7 | T1.4 (no other dependencies) |

---

## Suggested Sprint Sequencing

### Sprint 1 — Foundation + Safety
`T1.1 → T1.2 → T1.3 → T1.4 → T1.5 → T2.1 → T2.2 → T2.3 → T2.4`

Deliver: working connection config, dialect router, safety hooks, cost gate, scaffolded KB directory.
No analyst-facing commands yet, but the safety net is in place for everything that follows.

---

### Sprint 2 — Discovery Core
`T3.1 → T3.2 → T3.3 → T3.5 → T3.7`

Deliver: `/db-orient`, `/db-explain`, `/db-status`.
An analyst can now connect to an unfamiliar database and get a structured orientation.

---

### Sprint 3 — Work + Discovery Completion (parallel tracks)

**Track A (Work):** `T4.2 → T4.1 → T4.3 → T4.4`
Deliver: `/db-query`, `/db-gotchas`, Salesforce SOQL support.

**Track B (Discovery):** `T3.4 → T3.6`
Deliver: `/db-profile`, `/db-joins`.

---

### Sprint 4 — Documentation Layer
`T5.1 → T5.2 → T5.3 → T5.4 → T5.5 → T5.6`

Deliver: `/db-document`, `/db-capture`, post-session save prompts, KB index updater.
The knowledge base is now self-maintaining — analyst discoveries persist automatically.

**T5.7 (`kb-to-obsidian.py`) can be built anytime after Sprint 1** — it has no dependencies beyond the KB scaffold. Treat it as a parallel or background task, or defer it post-launch as the first community-facing contribution.

---

## Open Questions

- [ ] Should cost thresholds be per-database type or configurable per-analyst-role?
- [ ] Should `/db-orient` auto-save to `_schema-overview.md` or always prompt first?
- [ ] For Oracle: scope to specific schemas listed in `active.yaml`, or walk full accessible catalog?
- [ ] What is the right Salesforce equivalent for the `{schema}/{table}.md` structure — object type + object name?
- [ ] Should the KB index updater (`T5.6`) be a hook or an explicit command (`/db-index`)?
