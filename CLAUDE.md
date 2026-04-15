# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Is

This is the **source repository** for AnalystOS — a DB analyst framework and Claude Code extension that turns Claude into a senior data engineer assistant for SQL analysts.

**All skills and hooks are implemented.** The repo contains 18 installable skills (10 user-invocable slash commands + 8 internal reference skills), 4 hooks, per-project templates, and a working demo database.

Key reference documents:
- `docs/analystos-design.md` — Complete vision, principles, component specs
- `docs/setup-guide.md` — **Start here** to install and run the framework
- `starter-project/.claude/example_schema/` — Template files for new schemas and tables (kept in `.claude/` so they're not confused with real KB content)

## Core Design Principles

1. **Read-only by default** — All database interactions assume read-only; writes require explicit override
2. **Cost-aware before execution** — Flag expensive queries before running, not after
3. **Progressively calibrated** — Responses adjust to analyst expertise level
4. **Knowledge base as first-class artifact** — Markdown files in `db-knowledge/` are the product
5. **Database-agnostic core** — Oracle, Snowflake, AWS Athena, Salesforce (SOQL)
6. **Git-ready** — KB files are designed for version control and team sharing

## Architecture

The framework delivers three user modes (**Discovery**, **Work**, **Documentation**) through:

### User-Invocable Slash Commands
| Command | Purpose |
|---|---|
| `/db-orient` | Structured orientation to an unfamiliar database |
| `/db-profile` | Statistical profile of a table/column |
| `/db-explain` | Plain-English explanation of schema objects |
| `/db-query` | Natural-language → SQL conversion + validation |
| `/db-joins` | Discover and explain join paths |
| `/db-gotchas` | Load known issues from the knowledge base |
| `/db-document` | Draft/update data dictionary entries |
| `/db-capture` | Save a query + context to the knowledge base |
| `/db-status` | Show active connection and KB state |
| `/db-use` | Switch the active database connection |
| `/db-index` | Regenerate the KB index (`db-knowledge/README.md`) |

### Internal Reference Skills
| Skill | Purpose |
|---|---|
| `db-dialect` | Dialect-specific SQL syntax and metadata query templates |
| `db-introspect` | Extract structured schema metadata (columns, types, PKs, FKs) |
| `db-sample` | Retrieve representative row samples with value summaries |
| `db-cost-check` | Dialect-specific query cost estimation |
| `db-doc-writer` | Draft KB markdown entries (used by `/db-document`) |
| `db-explain-result` | Interpret query results and flag anomalies (used by `/db-query`) |
| `db-soql` | Salesforce SOQL reference and relationship traversal |

### Hooks (registered in `settings.json`)
- `pre-tool-use: db-safety` — Confirm read-only before any execution
- `pre-tool-use: db-cost-gate` — Flag queries that will scan large data (Oracle, Athena)
- `post-tool-use: db-capture-prompt` — Offer to save significant queries
- `post-tool-use: db-doc-prompt` — Offer to persist explanations

Session context loading (active connection + KB) is handled by the project-level `CLAUDE.md` startup checklist, not a hook.

### Knowledge Base Layout (target state)
```
db-knowledge/
├── README.md                         # Index of all connections/schemas
├── _gotchas.md                       # Cross-connection, project-wide warnings
├── _open-questions.md
├── {connection-name}/                # One dir per connection (matches name in active.yaml)
│   ├── _gotchas.md                   # Cross-schema gotchas for this connection
│   └── {schema}/
│       ├── _schema-overview.md       # Entity clusters, relationships
│       ├── {table}.md                # Per-table data dictionary
│       └── _queries/{name}.sql       # Named canonical queries
└── _external-docs/README.md
```

### Connection Config (target state, gitignored)
```
.claude/db-connections/
├── connections.example.yaml     # Committed template
└── active.yaml                  # Local only, gitignored
```

## Repo Structure

```
install/              # Copy to ~/.claude/ — global skills and hooks
  skills/             # All /db-* slash commands and model-invoked reference skills
  hooks/
    db-safety.py      # Pre-tool-use: blocks DDL/DML; override with "override read-only"
    db-cost-gate.py   # Pre-tool-use: gates expensive queries, asks for confirmation
  settings.json       # Hook registration template (merge into ~/.claude/settings.json)

starter-project/       # Ready-to-use project — copy this folder to start immediately
  CLAUDE.md                          # Project-level Claude instructions
  .claude/
    db-connections/
      connections.example.yaml       # All connection profiles template
      active.yaml                    # Pre-configured for demo DB (committed here only)
      demo-db/                       # Bundled SQLite demo database
        create-demo-db.sql
        demo.db
    example_schema/
      _schema-overview-template.md   # Copy → db-knowledge/{connection-name}/{schema}/_schema-overview.md
      _table-template.md             # Copy → db-knowledge/{connection-name}/{schema}/{table}.md
    scripts/
      install.py                     # Install/upgrade script (run from repo root)
      kb-to-obsidian.py              # Convert KB to an Obsidian-compatible vault
      athena_connect.py              # Athena CLI wrapper
      oracle_connect.py              # Oracle CLI wrapper
      snowflake_connect.py           # Snowflake CLI wrapper
      connectors/                    # Shared connector package
  db-knowledge/                      # Blank KB scaffold (README, _gotchas, _open-questions)
  .gitignore                         # Protects credentials in real projects

docs/
  setup-guide.md      # Full install walkthrough (global install + using starter-project)
```

## Implementation Status

All planned skills and hooks are implemented. See `docs/analystos-tasks.md` for historical sprint sequencing.

## Non-Obvious Implementation Notes

### `starter-project/.claude/db-connections/active.yaml` — gitignore behavior

This file is committed in the source repo via `git add -f`, bypassing the
`starter-project/.gitignore` rule that excludes `.claude/db-connections/active.yaml`.

**Why it's force-added here:** The demo `active.yaml` contains no real credentials — it
just points to the bundled `demo.db`. It's safe and necessary to commit so the
starter-project works out of the box.

**What happens when an analyst copies starter-project:** If they initialize their own git
repo in the copied folder, git will NOT track `active.yaml` (the gitignore applies).
This is correct — their `active.yaml` will eventually hold real credentials. If they
need to track a non-sensitive `active.yaml`, they use `git add -f` explicitly.

**Maintenance:** If `active.yaml` is ever regenerated in this source repo, stage it with
`git add -f starter-project/.claude/db-connections/active.yaml`.

---

## Supported Databases

| Database | Dialect | Metadata Source | Cost Signal |
|---|---|---|---|
| Oracle | Oracle SQL | `ALL_TABLES`, `ALL_COLUMNS`, `ALL_COMMENTS` | EXPLAIN PLAN row estimates |
| Snowflake | Snowflake SQL | `INFORMATION_SCHEMA`, `SHOW OBJECTS` | Manual via `/db-cost-check` (no automatic gate) |
| AWS Athena | Presto/Trino | Glue Catalog, `INFORMATION_SCHEMA` | Data scanned via EXPLAIN |
| Salesforce | SOQL | `describeSObject`, REST API | Record count / API governor limits |
