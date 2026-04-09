# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Is

This is the **source repository** for AnalystOS — a DB analyst framework and Claude Code extension that turns Claude into a senior data engineer assistant for SQL analysts.

**Sprint 1 (Foundation + Safety) is complete.** The repo now contains installable skills, hooks, per-project templates, and a working demo database. Slash commands (`/db-orient`, etc.) are planned for Sprint 2+.

Key reference documents:
- `docs/analystos-design.md` — Complete vision, principles, component specs
- `docs/analystos-tasks.md` — Task breakdown and sprint sequencing
- `docs/setup-guide.md` — **Start here** to install and run the framework

Key files:
- `docs/analystos-design.md` — Complete vision, principles, component specs, and knowledge base structure
- `docs/analystos-tasks.md` — Task breakdown and sprint sequencing (Sprints 1–4)
- `starter-project/.claude/example_schema/` — Template files for new schemas and tables (kept in `.claude/` so they're not confused with real KB content)

## Core Design Principles

1. **Read-only by default** — All database interactions assume read-only; writes require explicit override
2. **Cost-aware before execution** — Flag expensive queries before running, not after
3. **Progressively calibrated** — Responses adjust to analyst expertise level
4. **Knowledge base as first-class artifact** — Markdown files in `db-knowledge/` are the product
5. **Database-agnostic core** — Oracle, Snowflake, AWS Athena, Salesforce (SOQL)
6. **Git-ready** — KB files are designed for version control and team sharing

## Planned Architecture

The framework delivers three user modes (**Discovery**, **Work**, **Documentation**) through:

### Slash Commands (to be built as Claude Code skills)
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

### Hooks (to be registered in `settings.json`)
- `pre-tool-use: db-safety` — Confirm read-only before any execution
- `pre-tool-use: db-cost-gate` — Flag queries that will scan large data
- `post-tool-use: db-capture-prompt` — Offer to save significant queries
- `post-tool-use: db-doc-prompt` — Offer to persist explanations
- `session-start: db-context-loader` — Auto-load KB at session startup

### Knowledge Base Layout (target state)
```
db-knowledge/
├── README.md                    # Index of all schemas
├── _gotchas.md                  # Cross-schema warnings
├── _open-questions.md
├── {schema}/
│   ├── _schema-overview.md      # Entity clusters, relationships
│   ├── {table}.md               # Per-table data dictionary
│   └── _queries/{name}.sql      # Named canonical queries
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
  .claude/
    CLAUDE.md                        # Project-level Claude instructions
    db-connections/
      connections.example.yaml       # All connection profiles template
      active.yaml                    # Pre-configured for demo DB (committed here only)
      demo-db/                       # Bundled SQLite demo database
        create-demo-db.sql
        demo.db
    example_schema/
      _schema-overview-template.md   # Copy → db-knowledge/{schema}/_schema-overview.md
      _table-template.md             # Copy → db-knowledge/{schema}/{table}.md
  db-knowledge/                      # Blank KB scaffold (README, _gotchas, _open-questions)
  .gitignore                         # Protects credentials in real projects

docs/
  setup-guide.md      # Full install walkthrough (global install + using starter-project)

scripts/
  kb-to-obsidian.py   # Convert KB to an Obsidian-compatible vault
```

## Implementation Sequencing

- **Sprint 1 (complete):** Connection schema, dialect skill, CLAUDE.md template, KB scaffold, gitignore, safety/cost hooks, SQLite demo database
- **Sprint 2 (next):** `/db-orient`, `/db-explain`, `/db-status`, `db-introspect` + `db-sample` skills
- **Sprint 3:** `/db-query`, `/db-gotchas`, `/db-profile`, `/db-joins`, Salesforce support
- **Sprint 4:** `/db-document`, `/db-capture`, KB index auto-updater
- **Optional:** `scripts/kb-to-obsidian.py` — Obsidian vault export (no DB access needed)

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
| Snowflake | Snowflake SQL | `INFORMATION_SCHEMA`, `SHOW OBJECTS` | `BYTES_SCANNED` via query profile |
| AWS Athena | Presto/Trino | Glue Catalog, `INFORMATION_SCHEMA` | Data scanned via EXPLAIN |
| Salesforce | SOQL | `describeSObject`, REST API | Record count / API governor limits |
