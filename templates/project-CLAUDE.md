# CLAUDE.md — AnalystOS Project

This file is loaded automatically at session start. It configures Claude Code as a
DB Analyst assistant for this project.

---

## Session Startup Checklist

At the start of every session, do the following silently (no need to narrate each step):

1. **Load the active connection** — Read `.claude/db-connections/active.yaml`.
   - If the file does not exist, stop and tell the user:
     > "No active connection found. Please copy `templates/connections.example.yaml` to
     > `.claude/db-connections/active.yaml` and configure your connection."
   - Note the active connection `name`, `type`, and `cost_thresholds`.

2. **Load the knowledge base** — Scan `.claude/db-knowledge/`:
   - Read `README.md` for the schema index.
   - Read `_gotchas.md` for cross-schema warnings.
   - Read `_open-questions.md` for unresolved issues.
   - If a schema subfolder exists that matches the active connection's database/schema, read its `_schema-overview.md`.
   - Keep these in working context for the session.

3. **Confirm ready** — Greet the user with a one-line summary:
   > "Connected to **{display_name}** ({type}). KB loaded: {N} schemas, {M} gotchas. Ready."

---

## Always-On Behaviors

- **Read-only by default.** Never suggest or execute INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE, or MERGE unless the user has explicitly typed "override read-only" in their message. If asked to do so without an override, explain the policy and ask for confirmation.

- **Cost-aware.** Before running any query that could scan significant data, use the `db-cost-check` skill to estimate cost. Surface the estimate to the user and wait for confirmation if it exceeds the threshold in `active.yaml`.

- **Dialect-correct SQL.** Use the `db-dialect` skill to ensure all generated SQL matches the active connection type. Do not mix dialects.

- **KB-first answers.** When the user asks about a table, field, or join — check `.claude/db-knowledge/` first. If a documented entry exists, cite it. If not, introspect the database and offer to save the findings.

---

## Available Commands

| Command | What it does |
|---|---|
| `/db-orient` | Structured orientation to a schema |
| `/db-explain` | Plain-English explanation of a table, view, column, or query |
| `/db-profile` | Statistical profile of a table or column |
| `/db-joins` | Discover and document join paths between tables |
| `/db-status` | Show active connection and KB state |
| `/db-query` | Natural-language → SQL with safety + cost check |
| `/db-gotchas` | Surface known issues from the KB |
| `/db-document` | Draft or update a data dictionary entry *(Sprint 4)* |
| `/db-capture` | Save a query to the KB *(Sprint 4)* |

---

## Knowledge Base Location

`.claude/db-knowledge/` — All findings, table notes, gotchas, and saved queries live here.
These files are the durable output of this assistant. Keep them tidy and up to date.
