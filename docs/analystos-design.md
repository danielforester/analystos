# AnalystOS
## Project Design Document

**Version:** 0.1 (Draft)
**Status:** Pre-implementation
**Last Updated:** 2026-03-31

---

## 1. Vision

> *An analyst working with Claude Code should feel like they have a senior data engineer sitting next to them who already knows the database, remembers every question that's been asked before, and can draft a query, explain a schema, or write a data dictionary entry without being asked twice.*

This framework turns Claude Code into a structured, opinionated assistant for SQL analysts working across heterogeneous database environments. It prioritizes **safety** (read-only by default, cost guardrails), **discovery** (rapid orientation to unfamiliar schemas), **knowledge accumulation** (findings persist and compound over time), and **portability** (works across Oracle, Snowflake, Athena, and Salesforce SOQL).

---

## 2. Design Principles

| Principle | Description |
|---|---|
| **Read-only by default** | All database interactions assume a read-only posture. Write operations require explicit override and confirmation. |
| **Cost-aware** | Expensive queries (large scans, missing filters on partitioned tables) are flagged before execution, not after. |
| **Progressively revealing** | New analysts get orientation and explanation; experienced ones get out-of-the-way assistance. Claude calibrates to apparent expertise. |
| **The knowledge base is the product** | Markdown files, query examples, and field notes are first-class artifacts — not side effects. The framework succeeds when those files are consulted directly. |
| **Database-agnostic core, dialect-aware execution** | Commands and skills work across all supported databases. Dialect differences (SQL syntax, metadata queries, cost signals) are handled in a configuration layer. |
| **Introspection-first** | Initial knowledge comes from what's actually in the database — schema, comments, data samples. External documentation is a future enhancement layered on top. |
| **Git-ready from day one** | Even when sharing via shared drive, the knowledge base structure is designed to be version-controlled when the team is ready. |

---

## 3. Supported Databases

| Database | Query Dialect | Metadata Source | Cost Signal |
|---|---|---|---|
| **Oracle** | SQL (Oracle dialect) | `ALL_TABLES`, `ALL_COLUMNS`, `ALL_COMMENTS` | Estimated rows via `EXPLAIN PLAN` |
| **Snowflake** | SQL (Snowflake dialect) | `INFORMATION_SCHEMA`, `SHOW OBJECTS` | `BYTES_SCANNED` via query profile |
| **AWS Athena** | SQL (Presto/Trino dialect) | Glue Data Catalog, `INFORMATION_SCHEMA` | Data scanned estimate via `EXPLAIN` |
| **Salesforce** | SOQL | `describeSObject`, REST API metadata | Record count / API limits |

Connection profiles for each database are stored in `.claude/db-connections/` (credentials via environment variables or secret manager — never stored in plain text).

---

## 4. User Modes

The framework serves analysts in three distinct modes, often within a single session:

### 4.1 Discovery Mode
*"I'm new to this database, help me understand it."*

The analyst needs orientation before they can work effectively. Claude should offer a structured walkthrough: what schemas exist, what the major entity clusters are, likely grain of key tables, and notable relationships — drawing on schema metadata and sampled data, not just raw `INFORMATION_SCHEMA` dumps.

### 4.2 Work Mode
*"I know roughly what I want, help me write and validate it."*

The analyst has a question in mind. Claude assists with query construction, warns about known gotchas, and interprets results rather than just returning them.

### 4.3 Documentation Mode
*"Let's capture what we've learned so others don't start from zero."*

The analyst wants to record a discovery — a table's true grain, a surprising field encoding, a canonical join path. Claude helps draft structured documentation that lands in the knowledge base.

---

## 5. Framework Components

### 5.1 Slash Commands

| Command | Mode | Description |
|---|---|---|
| `/db-orient` | Discovery | Structured orientation to an unfamiliar database or schema. Summarizes entity clusters, major tables, key relationships, and any available comments. |
| `/db-profile` | Discovery / Work | Statistical profile of a table or column: row count, null rate, distinct values, min/max, sample values. Cost-checked before execution. |
| `/db-explain` | Discovery / Work | Plain-English explanation of a table, column, view, or stored query. Synthesizes schema metadata, comments, and sample data. |
| `/db-query` | Work | Natural-language to SQL. Validates query before execution, checks for safety (no DDL/DML), and estimates cost on supported databases. |
| `/db-joins` | Work | Discovers and explains join paths between two or more tables. Checks cardinality and flags potential fan-out. |
| `/db-gotchas` | Work | Lists known issues for a table or schema from the project knowledge base (soft deletes, timezone fields, status encodings, etc.). |
| `/db-document` | Documentation | Drafts or updates a data dictionary entry for a table or field, combining introspected metadata with analyst-provided context. |
| `/db-capture` | Documentation | Saves the current query and its context (purpose, result interpretation, caveats) as a named example in the knowledge base. |
| `/db-status` | Any | Shows active connection, current schema context, and loaded knowledge base entries. |

---

### 5.2 Claude Code Skills

Skills are reusable prompt libraries invoked internally by slash commands or hooks.

| Skill | Description |
|---|---|
| `db-introspect` | Dialect-aware metadata extraction. Generates the right `INFORMATION_SCHEMA` / system catalog queries for the active database. |
| `db-sample` | Safe, cost-controlled data sampling. Uses `LIMIT`/`FETCH FIRST`, avoids full scans. Adapts sampling strategy per dialect. |
| `db-cost-check` | Pre-execution cost assessment. Parses query, identifies risk signals (missing WHERE on large tables, cross joins, no partition filter), and returns a warning or approval. |
| `db-explain-result` | Post-execution result interpretation. Given a query and its result set, synthesizes what the numbers mean and flags anomalies. |
| `db-dialect` | Translation layer. Converts generic SQL patterns to dialect-specific syntax (e.g., `LIMIT` → `ROWNUM` for Oracle). |
| `db-soql` | Salesforce-specific skill. Handles SOQL construction, `describeSObject` calls, relationship queries, and API limit awareness. |
| `db-doc-writer` | Drafts structured documentation from introspection results and analyst input. Outputs to the knowledge base markdown format. |

---

### 5.3 Hooks

| Hook | Trigger | Behavior |
|---|---|---|
| `pre-tool-use: db-safety` | Before any database tool call | Confirms query is SELECT-only (no DDL, DML, or DCL). Prompts for explicit confirmation if write syntax is detected. |
| `pre-tool-use: db-cost-gate` | Before any database tool call | Runs cost-check skill. Surfaces estimated scan size / row count. Requires confirmation above a configurable threshold. |
| `post-tool-use: db-capture-prompt` | After a successful query | If result appears significant (non-trivial output, took >N seconds), offers to save the query to the knowledge base with `/db-capture`. |
| `post-tool-use: db-doc-prompt` | After `/db-explain` or `/db-orient` | Offers to persist the explanation as a markdown entry in `db-knowledge/`. |
| `session-start: db-context-loader` | On session start | Detects active database connection and loads relevant knowledge base entries (schema summaries, known gotchas) into context via `CLAUDE.md` injection. |

---

### 5.4 Knowledge Base Structure

All knowledge lives in `db-knowledge/` as plain markdown files. This directory is the durable, shareable, version-controllable product of the framework.

```
.claude/
├── CLAUDE.md                        # Project-level Claude instructions (auto-loads KB context)
├── db-connections/
│   ├── connections.example.yaml     # Template — never store real credentials here
│   └── active.yaml                  # Symlink or pointer to active connection profile
└── db-knowledge/
    ├── README.md                    # Index of all documented schemas and tables
    ├── _gotchas.md                  # Cross-schema gotchas and warnings
    ├── _open-questions.md           # Unresolved questions for future investigation
    ├── {schema}/
    │   ├── _schema-overview.md      # Entity clusters, domain summary, key relationships
    │   ├── {table}.md               # Per-table data dictionary entry (see format below)
    │   └── _queries/
    │       └── {query-name}.sql     # Named, annotated canonical queries
    └── _external-docs/              # Placeholder: links or imports from Confluence, dbt, ERDs
        └── README.md                # Instructions for future external doc ingestion
```

#### Per-Table Entry Format (`{table}.md`)

```markdown
# TABLE_NAME

**Schema:** schema_name
**Database:** Snowflake / Oracle / Athena / Salesforce
**Last Documented:** YYYY-MM-DD
**Documented By:** [analyst name or "introspection"]

## Purpose
Plain-English description of what this table represents.

## Grain
One row per [entity]. E.g., "One row per order line item."

## Key Fields
| Column | Type | Description | Notes |
|--------|------|-------------|-------|
| id | INTEGER | Primary key | Never null |
| status | VARCHAR | Order status | See Gotchas |

## Relationships
- Joins to `orders` on `order_id` (many-to-one)
- Joins to `products` on `product_id` (many-to-one)

## Gotchas
- `status = 'D'` means soft-deleted; always filter `status != 'D'`
- `created_date` is stored in UTC; convert for local reporting

## Sample Query
```sql
SELECT ...
```

## Open Questions
- [ ] What does status = 'P' mean? Needs confirmation from data owner.
```

---

### 5.5 `CLAUDE.md` Integration

The project-level `CLAUDE.md` file is extended with a database context block that Claude Code reads at session start:

```markdown
## Database Context

Active connection: See `.claude/db-connections/active.yaml`
Knowledge base: `db-knowledge/`

When working on database tasks:
1. Always confirm connection is read-only before executing queries
2. Run cost-check before any query against tables with > 1M rows (see `_gotchas.md`)
3. Load the relevant schema overview before answering questions about a table
4. Offer to document any new findings via `/db-document` or `/db-capture`
```

---

## 6. Safety & Guardrails

### 6.1 Read-Only Enforcement
- All database connections should use read-only credentials by default
- The `db-safety` hook parses every query string for DDL (`CREATE`, `DROP`, `ALTER`, `TRUNCATE`) and DML (`INSERT`, `UPDATE`, `DELETE`, `MERGE`) keywords before execution
- If detected, execution is blocked and the analyst must type an explicit override phrase
- Salesforce: SOQL is inherently read-only; REST API write calls are blocked by the same mechanism

### 6.2 Cost Guardrails
- **Athena:** Estimate bytes scanned using `EXPLAIN`. Warn if > configurable threshold (default: 1 GB). Remind analyst to use partition filters.
- **Snowflake:** Warn on queries with no `WHERE` clause against tables with > configurable row threshold. Suggest `LIMIT` for exploration.
- **Oracle:** Run `EXPLAIN PLAN` pre-execution. Warn on full table scans of large tables.
- **Salesforce:** Warn on SOQL queries without selective filters (non-indexed `WHERE` clauses) that risk hitting governor limits.
- Thresholds are configurable per-project in `.claude/db-connections/active.yaml`

### 6.3 Credential Safety
- Connection strings, passwords, and tokens are **never** stored in knowledge base markdown files
- The framework uses environment variables or a designated secrets file (`.env`, excluded from git via `.gitignore`)
- `connections.example.yaml` ships with the framework; `active.yaml` is in `.gitignore`

---

## 7. Collaboration & Sharing Model

### Phase 1: Shared Drive (Current)
- `db-knowledge/` is a folder shared via network drive or cloud storage (e.g., SharePoint, Google Drive)
- Analysts work from local copies and manually sync changes
- Risk of edit conflicts is low given mostly-additive, file-per-table structure

### Phase 2: Git (Target)
- `db-knowledge/` becomes a git repository (or subdirectory of a larger monorepo)
- PRs used to review new table documentation and canonical query additions
- CI can validate markdown format and flag undocumented tables
- Knowledge base becomes auditable and diffable over time

The file structure is designed to be git-friendly from day one: one file per table, append-only gotchas, no binary files.

---

## 8. External Documentation (Future Placeholder)

*Initial implementation relies exclusively on introspection. This section reserves space for future integration.*

Planned future integrations to layer onto the introspection-first base:

- **dbt models:** Parse `schema.yml` files to pre-populate table and column descriptions
- **ERD files:** Ingest draw.io or dbdiagram.io exports to bootstrap relationship documentation
- **Confluence / Notion:** Link or import narrative documentation into `_external-docs/`
- **Data catalogs:** Pull from Alation, Collibra, or Atlan if available

When external docs exist, they should *supplement* introspected knowledge, not replace it. The source of truth for field-level meaning should always include a verification pass against actual data.

---

### 8.1 Optional: Obsidian Visualization Layer

The knowledge base is designed as plain, portable markdown — renderable in GitHub, VS Code, any static site generator, and any text editor. **Obsidian is not a dependency.** It is an opt-in visualization layer for analysts who want a graph view of table relationships and a richer browsing experience outside of Claude Code.

#### Why It Fits

The `db-knowledge/` directory is structurally identical to an Obsidian vault: a folder of interlinked markdown files, one file per entity, with consistent naming. An analyst who opens the folder as an Obsidian vault immediately gets a navigable graph of table relationships — effectively a live, self-maintaining ERD — without any additional tooling in the framework itself.

#### Why We Don't Build for It by Default

Obsidian uses `[[wikilink]]` syntax for internal navigation. Standard markdown uses relative paths: `[orders](../orders/orders.md)`. These are incompatible — wikilinks render as broken text outside Obsidian, and relative links don't produce graph edges inside it. Committing wikilinks to the canonical KB would break rendering on GitHub, in VS Code, and in any future static site export. The canonical KB therefore uses **standard markdown links only**.

#### The Conversion Approach

A standalone Python script (`scripts/kb-to-obsidian.py`) handles the translation for analysts who want Obsidian. It is intentionally decoupled from Claude Code and requires no database access.

**What the script does:**

- Converts relative markdown links to `[[wikilinks]]` throughout all KB files
- Injects YAML frontmatter into files that don't already have it, promoting parsed header fields (`schema`, `database`, `last_documented`, etc.) into a `---` block — enabling Obsidian's Dataview plugin for live auto-generated index tables
- Optionally converts `⚠️` warning prefixes to Obsidian callout syntax (`> [!warning]`) for better visual rendering
- Writes all output to a separate `_obsidian-vault/` directory — **never modifies source KB files**

**Script interface:**

```bash
python scripts/kb-to-obsidian.py \
  --source  db-knowledge/ \
  --output  .claude/_obsidian-vault/ \
  --frontmatter \        # inject YAML frontmatter
  --callouts \           # convert ⚠️ patterns to Obsidian callouts
  --watch                # re-run automatically on KB file changes
```

The `--watch` flag is particularly useful during active Claude Code sessions: the analyst runs the script once in the background and their Obsidian vault stays in sync as new KB files are written.

#### Git and Sharing

The `_obsidian-vault/` output directory is gitignored — it is a generated artifact, not a source file. The `.obsidian/` config folder (created when an analyst opens a vault) is also gitignored by default, keeping personal workspace settings out of the shared repository.

```gitignore
# Obsidian opt-in layer (generated — do not commit)
.claude/_obsidian-vault/
.obsidian/workspace.json
.obsidian/workspace-mobile.json
```

If a team wants to share a baseline Obsidian configuration (pinned plugins, graph settings), they may choose to commit the rest of `.obsidian/` while keeping the workspace files excluded. This is left to team preference.

#### Dataview and the YAML Frontmatter Convention

If YAML frontmatter is present in KB files, the Obsidian Dataview plugin can auto-generate live index tables — making the `README.md` index dynamic rather than manually maintained. This is an optional enhancement: the framework's `db-doc-writer` skill can be configured to emit frontmatter by default, but does not require it for the canonical KB to function.

Recommended frontmatter fields for per-table files:

```yaml
---
schema: orders
database: snowflake
grain: one row per order line item
status: trusted          # draft | reviewed | trusted
last_documented: 2026-03-15
documented_by: daniel
---
```

#### What Obsidian Does Not Replace

The graph view reflects *link presence*, not *relationship semantics*. It shows that `orders.md` and `line_items.md` are connected, but cannot convey cardinality, join type, required pre-filters, or fan-out risk. That detail lives in the structured relationship blocks in Section 3 of each `_schema-overview.md`. Obsidian is a navigation and orientation tool; the markdown files remain the authoritative reference.

---

## 9. Out of Scope (v1)

- Python/pandas workflow support
- Write operations of any kind
- Data lineage tracking
- PII / sensitive data detection (flagged for future)
- Automated documentation refresh / staleness detection
- Integration with BI tools (Tableau, Looker, etc.)
- Multi-database join support (federated queries)

---

## 10. Open Questions

- [ ] Should cost thresholds be per-database or per-analyst-role?
- [ ] What is the right format for Salesforce "table" documentation given its object model differs from relational?
- [ ] Should `/db-orient` produce a summary document automatically, or prompt the analyst to save?
- [ ] For Oracle, should the framework scope to a specific set of schemas, or walk the full accessible catalog?
- [ ] What confirmation UX is right for the cost gate — block entirely, or show estimate and require a "proceed" response?

---

## Appendix A: File Glossary

| Path | Purpose |
|---|---|
| `.claude/CLAUDE.md` | Claude Code project instructions; loads DB context at session start |
| `.claude/db-connections/active.yaml` | Active connection profile (gitignored) |
| `.claude/db-connections/connections.example.yaml` | Template for connection profiles |
| `db-knowledge/README.md` | Index of all documented connections/schemas |
| `db-knowledge/_gotchas.md` | Cross-connection, project-wide warnings |
| `db-knowledge/_open-questions.md` | Unresolved questions |
| `db-knowledge/{connection-name}/_gotchas.md` | Cross-schema gotchas for this connection |
| `db-knowledge/{connection-name}/{schema}/_schema-overview.md` | Schema-level orientation document |
| `db-knowledge/{connection-name}/{schema}/{table}.md` | Per-table data dictionary entry |
| `db-knowledge/{connection-name}/{schema}/_queries/{name}.sql` | Named canonical queries |
| `db-knowledge/_external-docs/` | Placeholder for future external doc ingestion |

---

## Appendix B: Dialect Reference Card

| Feature | Oracle | Snowflake | Athena | Salesforce (SOQL) |
|---|---|---|---|---|
| Limit rows | `FETCH FIRST N ROWS ONLY` | `LIMIT N` | `LIMIT N` | `LIMIT N` |
| Current timestamp | `SYSDATE` | `CURRENT_TIMESTAMP()` | `NOW()` | `TODAY()` (date only) |
| String concat | `\|\|` or `CONCAT()` | `\|\|` or `CONCAT()` | `\|\|` or `CONCAT()` | Not applicable |
| Metadata tables | `ALL_TABLES`, `ALL_COLUMNS` | `INFORMATION_SCHEMA` | Glue / `INFORMATION_SCHEMA` | `describeSObject` |
| Cost estimation | `EXPLAIN PLAN` | Query profile / `EXPLAIN` | `EXPLAIN` (bytes scanned) | Governor limits / row count |
| Soft-delete convention | Varies — check gotchas | Varies — check gotchas | Varies — check gotchas | `IsDeleted = true` (standard) |
