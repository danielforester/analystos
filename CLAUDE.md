# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Is

This is the **source repository** for AnalystOS — a DB analyst framework and Claude Code extension that turns Claude into a senior data engineer assistant for SQL analysts.

**All skills and hooks are implemented.** The repo contains 18 installable skills (11 user-invocable slash commands + 7 internal reference skills), 4 hooks, per-project templates, and a working demo database.

Key reference documents:
- `docs/analystos-design.md` — Complete vision, principles, component specs
- `docs/setup-guide.md` — **Start here** to install and run the framework
- `starter-project/.claude/example_schema/` — Template files for new schemas and tables (kept in `.claude/` so they're not confused with real KB content)

## Core Design Principles

1. **Read-only by default** — All database interactions assume read-only; writes require explicit override
2. **Cost-aware before execution** — Flag expensive queries before running, not after
3. **Progressively calibrated** — Responses adjust to analyst expertise level
4. **Knowledge base as first-class artifact** — Markdown files in `db-knowledge/` are the product
5. **Database-agnostic core** — SQLite, Oracle, Snowflake, AWS Athena, Salesforce (SOQL)
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
- `pre-tool-use: db-safety` — Block DDL/DML/DCL before execution; override with `"override read-only"`
- `pre-tool-use: db-cost-gate` — Flag queries that will scan large data (Oracle, Athena, Snowflake); bypass with `"cost confirmed"`
- `post-tool-use: db-capture-prompt` — Offer to save significant queries (configurable `capture_row_floor`); deduplicated by SQL hash
- `post-tool-use: db-doc-prompt` — Offer to persist `/db-explain` or `/db-orient` output as KB entries; deduplicated per session (PID)

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
├── connections.example.yaml     # Committed template (all database types with all auth modes)
└── active.yaml                  # Local only, gitignored
```

**Transport modes:** Each connection specifies `transport: direct` (skills invoke connector scripts via Bash) or `transport: mcp` (skills route through an MCP server; set `mcp_server` to the registered server name).

## Repo Structure

```
install/              # Copy to ~/.claude/ — global skills and hooks
  skills/             # All /db-* slash commands and model-invoked reference skills
    db-capture/SKILL.md
    db-cost-check/SKILL.md
    db-dialect/SKILL.md
    db-doc-writer/SKILL.md
    db-document/SKILL.md
    db-explain-result/SKILL.md
    db-explain/SKILL.md
    db-gotchas/SKILL.md
    db-index/SKILL.md
    db-introspect/SKILL.md
    db-joins/SKILL.md
    db-orient/SKILL.md
    db-profile/SKILL.md
    db-query/SKILL.md
    db-sample/SKILL.md
    db-soql/SKILL.md
    db-status/SKILL.md
    db-use/SKILL.md
  hooks/
    db-safety.py        # Pre-tool-use: blocks DDL/DML; override with "override read-only"
    db-cost-gate.py     # Pre-tool-use: gates expensive queries, asks for confirmation
    db-capture-prompt.py  # Post-tool-use: offers to save queries to KB
    db-doc-prompt.py      # Post-tool-use: offers to save explanations to KB
  settings.json         # Hook registration template (merge into ~/.claude/settings.json)

starter-project/       # Ready-to-use project — copy this folder to start immediately
  CLAUDE.md                          # Project-level Claude instructions (session startup + always-on behaviors)
  .claude/
    db-connections/
      connections.example.yaml       # All connection profiles template (all 5 DB types, all auth modes)
      active.yaml                    # Pre-configured for demo DB (committed here only)
      demo-db/
        README.md                    # Demo DB walkthrough
        create-demo-db.sql           # Schema + seed data for demo.db
        demo.db                      # Bundled SQLite demo database
        demo-connections.yaml        # Standalone demo connection config
    example_schema/
      _schema-overview-template.md  # Copy → db-knowledge/{conn}/{schema}/_schema-overview.md
      _table-template.md            # Copy → db-knowledge/{conn}/{schema}/{table}.md
    scripts/
      install.py                    # Install/upgrade script (run from repo root)
      kb-to-obsidian.py             # Convert KB to an Obsidian-compatible vault
      athena_connect.py             # Athena CLI wrapper
      oracle_connect.py             # Oracle CLI wrapper
      salesforce_connect.py         # Salesforce CLI wrapper (sf_cli, oauth_web, playwright, password, access_token)
      snowflake_connect.py          # Snowflake CLI wrapper
      connectors/                   # Shared connector package (imported by *_connect.py wrappers)
        __init__.py
        common.py                   # Shared CLI arg parsing, output formatting
        athena.py
        oracle.py
        salesforce.py
        snowflake.py
        tests/                      # Connector unit tests
          test_athena.py
          test_oracle.py
          test_snowflake.py
  db-knowledge/                      # Blank KB scaffold (README, _gotchas, _open-questions)
  .gitignore                         # Protects credentials in real projects

docs/
  setup-guide.md           # Full install walkthrough (global install + using starter-project)
  analystos-design.md      # Complete design doc (vision, principles, component specs)
  analystos-tasks.md       # Historical sprint sequencing
  analyst-os-infographic*  # Visual overviews (PNG, SVG, JSX source)
```

## Development Workflow

### Installing (for local testing)

Run the install script from the repo root:
```bash
# Install/upgrade global components (skills + hooks + settings.json merge)
python starter-project/.claude/scripts/install.py

# Preview changes without writing
python starter-project/.claude/scripts/install.py --dry-run

# Also copy connector scripts into a project directory
python starter-project/.claude/scripts/install.py --project-dir /path/to/your/project
```

The install script:
1. Copies `install/skills/` → `~/.claude/skills/`
2. Copies `install/hooks/` → `~/.claude/hooks/` (makes them executable on macOS/Linux)
3. Merges `install/settings.json` hook entries into `~/.claude/settings.json` (idempotent; skips already-registered hooks)
4. Optionally copies connector scripts + `connectors/` package into `{project}/.claude/scripts/`

### Adding a New Skill

1. Create `install/skills/{db-skillname}/SKILL.md`
2. Set `user-invocable: true` (slash command) or `user-invocable: false` (internal reference skill) in the frontmatter
3. Re-run `install.py` to deploy to `~/.claude/skills/`
4. Add to the appropriate table in this CLAUDE.md and in `starter-project/CLAUDE.md`

### Adding a New Connector

1. Add a new connector module at `starter-project/.claude/scripts/connectors/{dialect}.py`
2. Add a CLI wrapper at `starter-project/.claude/scripts/{dialect}_connect.py`
3. Add the connection profile template to `starter-project/.claude/db-connections/connections.example.yaml`
4. Update `db-dialect/SKILL.md` with dialect-specific metadata queries and SQL syntax
5. Add connector tests at `starter-project/.claude/scripts/connectors/tests/test_{dialect}.py`

### Running Connector Tests

```bash
cd starter-project/.claude/scripts
python -m pytest connectors/tests/
```

### Working With the Demo Database

The demo database (`starter-project/.claude/db-connections/demo-db/demo.db`) is a SQLite database pre-configured in `active.yaml`. To reset it:
```bash
cd starter-project/.claude/db-connections/demo-db
sqlite3 demo.db < create-demo-db.sql
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

### Hook deduplication

`db-doc-prompt.py` deduplicates per session using a `/tmp/db-doc-prompt-{PID}.seen` file. `db-capture-prompt.py` deduplicates by SHA1 hash of normalized SQL stored in `/tmp/db-capture-prompt-{PID}.seen`. Both files are cleaned up at process exit.

### install.py — connector scripts are project-local, not global

Connector scripts (`athena_connect.py`, `oracle_connect.py`, etc.) are intentionally project-local. Skills call them via relative paths (e.g. `python .claude/scripts/oracle_connect.py`). Running `install.py` without `--project-dir` does NOT install or update them — only the global skills and hooks are affected.

### Salesforce auth modes

`salesforce_connect.py` supports five auth modes: `sf_cli` (reuse an existing SF CLI session — recommended), `oauth_web` (browser OAuth 2.0 with Connected App), `playwright` (browser automation via Playwright, handles SSO/MFA), `password` (username + password + security token), and `access_token` (paste a Bearer token). The `playwright` mode requires `pip install playwright && playwright install chromium`.

---

## Supported Databases

| Database | Dialect | Metadata Source | Cost Signal |
|---|---|---|---|
| SQLite | SQLite SQL | `sqlite_master`, `PRAGMA table_info()` | No thresholds (local file) |
| Oracle | Oracle SQL | `ALL_TABLES`, `ALL_COLUMNS`, `ALL_COMMENTS` | EXPLAIN PLAN row estimates |
| Snowflake | Snowflake SQL | `INFORMATION_SCHEMA`, `SHOW OBJECTS` | EXPLAIN USING TABULAR (bytesAssigned) |
| AWS Athena | Presto/Trino | Glue Catalog, `INFORMATION_SCHEMA` | Data scanned via EXPLAIN |
| Salesforce | SOQL | `describeSObject`, REST API | Record count / API governor limits |
