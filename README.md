# AnalystOS

**A Claude Code extension that turns Claude into a senior data engineer assistant for SQL analysts.**

> *An analyst working with Claude Code should feel like they have a senior data engineer sitting next to them — one who already knows the database, remembers every question that's been asked before, and can draft a query, explain a schema, or write a data dictionary entry without being asked twice.*

---

## What it does

AnalystOS adds structured slash commands, safety hooks, and a persistent knowledge base to Claude Code. It covers three modes of analyst work:

| Mode | You're doing... | Key commands |
|---|---|---|
| **Discovery** | Learning an unfamiliar database | `/db-orient`, `/db-explain`, `/db-profile` |
| **Work** | Writing and validating queries | `/db-query`, `/db-joins`, `/db-gotchas` |
| **Documentation** | Capturing what you've learned | `/db-document`, `/db-capture`, `/db-index` |

### Slash commands

| Command | What it does |
|---|---|
| `/db-orient` | Structured orientation to a schema — entity clusters, relationships, key tables |
| `/db-explain <table>` | Plain-English explanation of a table, column, or view |
| `/db-profile <table>` | Row count, null rates, distinct values, min/max, top-N |
| `/db-query` | Natural language → dialect-correct SQL, cost-checked before execution |
| `/db-joins <t1> <t2>` | Discover join paths, check cardinality, flag fan-out risk |
| `/db-gotchas <table>` | Surface known traps from the knowledge base (soft deletes, encodings, etc.) |
| `/db-document <table>` | Draft or update a data dictionary entry |
| `/db-capture` | Save a query + context as a named canonical example |
| `/db-status` | Show active connection, schema scope, and KB inventory |
| `/db-use <name>` | Switch the active database connection |

### Safety built in

Two hooks run automatically on every database interaction:

- **db-safety** — blocks DDL and DML (`DROP`, `DELETE`, `INSERT`, etc.) before execution; requires an explicit override to proceed
- **db-cost-gate** — flags expensive queries (full table scans, missing partition filters) on Oracle and Athena before they run, not after

### A knowledge base that compounds

Findings accumulate in `db-knowledge/` — plain markdown files, one per table. Over time this becomes a team-shared, git-versioned reference that Claude reads at session start. The knowledge base is the durable product; Claude is the tool that builds it.

---

## Supported databases

| Database | Notes |
|---|---|
| **SQLite** | Bundled demo database — no credentials needed |
| **Oracle** | Full support: dialect, metadata, EXPLAIN PLAN cost estimation |
| **Snowflake** | Full support: INFORMATION_SCHEMA, bytes-scanned cost gate |
| **AWS Athena** | Full support: Glue catalog, partition-filter cost gate |
| **Salesforce** | SOQL support: describeSObject, relationship traversal, governor limit awareness |

---

## Install

**Prerequisites:** Claude Code, Python 3.9+, `pip install pyyaml`

### 1. Install skills and hooks globally

```bash
git clone https://github.com/danielforester/analystos.git
cd analystos
python scripts/install.py
```

This copies 18 skills and 4 hook scripts into `~/.claude/` and merges the hooks block into `~/.claude/settings.json`. Safe to re-run for upgrades. Use `--dry-run` to preview changes first.

### 2. Set up a project

```bash
cp -r starter-project/ ~/my-analyst-project/
cd ~/my-analyst-project/
```

### 3. Open Claude Code

```bash
code .    # VS Code with Claude Code extension
# or: claude  # Claude Code CLI
```

Claude will greet you with the active connection and KB state.

---

## Try the demo

`starter-project` ships with a pre-built SQLite demo database — no credentials or cloud access needed. After completing the install above, open Claude Code inside `my-analyst-project/` and try:

```
/db-status                                       — confirm connection and KB state
/db-orient                                       — full orientation to the demo schema
/db-explain sales_orders                         — what is this table?
/db-explain column: sales_customers.deleted_at   — understand the soft-delete pattern
/db-joins sales_orders sales_customers           — how do these tables connect?
/db-profile sales_order_items                    — statistical shape of the line items table
What are the top customers by revenue this year? — natural language works too
```

To connect to your own database, edit `.claude/db-connections/active.yaml` — see [docs/setup-guide.md](docs/setup-guide.md) for connection profiles and credential setup.

---

## How it's structured

```
install/          — copy to ~/.claude/ (the install script does this)
  skills/         — 18 Claude Code skills (/db-* commands + internal reference skills)
  hooks/          — 4 Python hook scripts (safety + cost gate)
  settings.json   — hook registration template

starter-project/  — copy this to start a new analyst project
  .claude/
    CLAUDE.md                     — project instructions (auto-loads KB on session start)
    db-connections/
      active.yaml                 — active connection config (gitignored in your project)
      connections.example.yaml    — template for all connection types
      demo-db/demo.db             — bundled SQLite demo database
  db-knowledge/                   — knowledge base scaffold (README, gotchas, open questions)

docs/
  setup-guide.md  — full install walkthrough, MCP setup, troubleshooting
  analystos-design.md — complete design spec and architecture
```

---

## Team sharing

The knowledge base is plain markdown — designed to be version-controlled and shared:

- **Shared drive:** Put `db-knowledge/` on Dropbox or Google Drive. Works today, no git required.
- **Git repo:** Commit `db-knowledge/` and `.claude/CLAUDE.md`. Each analyst keeps their own `active.yaml` locally (gitignored). Open PRs for new schema overviews or significant gotcha additions.

**Always commit:** `db-knowledge/`, `.claude/CLAUDE.md`, `.claude/db-connections/connections.example.yaml`  
**Never commit:** `.claude/db-connections/active.yaml` (contains credentials)

---

## Optional: Obsidian visualization

The knowledge base is structurally identical to an Obsidian vault. Run `scripts/kb-to-obsidian.py` to generate a converted vault with wikilinks and YAML frontmatter — the result is a navigable graph of table relationships and a live ERD. The script never modifies source KB files; output goes to a separate gitignored directory.
