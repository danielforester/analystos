---
name: db-status
description: Show the current DB Analyst session state — active connection, schema scope, cost thresholds, and knowledge base inventory. No database calls required. Run /db-status at any time to confirm what is loaded and ready.
---

# /db-status

**Invocation:** `/db-status`

Use this command to get an instant snapshot of the current session state: what database
is connected, what knowledge base entries are loaded, and what safety settings are active.
This command never makes database calls — it only reads local files.

---

## Behavior

1. Read the active connection from `.claude/db-connections/active.yaml`
2. Scan the knowledge base at `.claude/db-knowledge/`
3. Report the session state in a compact, scannable format

---

## Step 1: Read Active Connection

Read `.claude/db-connections/active.yaml`.

Find the connection whose `name` matches the `active:` field at the top of the file.
Extract:

| Field | Source |
|-------|--------|
| Display name | `display_name` |
| Type | `type` |
| Schema scope | `schema_scope` (Oracle), `database` (Athena), `sqlite.path` (SQLite), or "all accessible" |
| Read-only | `read_only` (true/false) |
| Cost threshold | `cost_thresholds.warn_rows` (Oracle) or `cost_thresholds.warn_bytes` (Athena) or null (SQLite) |

**If `active.yaml` does not exist:**

```
DB Analyst Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connection  : Not configured

No active connection found.
Copy `templates/connections.example.yaml` to `.claude/db-connections/active.yaml`
and configure your connection to get started.

Run `/db-orient` once connected to orient yourself to the database.
```

Stop here if no connection is found.

---

## Step 2: Scan Knowledge Base

Scan `.claude/db-knowledge/`. Collect:

- **Schemas with overview:** Count subdirectories that contain a `_schema-overview.md` file
- **Tables documented:** Count all `{table}.md` files (exclude files starting with `_`)
- **Active gotchas:** Count `## ⚠️` or `### ⚠️` headings in `_gotchas.md` (root and all schema subdirs)
- **Open questions:** Count unchecked `- [ ]` items in `_open-questions.md` (root and all schema subdirs)

If `.claude/db-knowledge/` does not exist or is empty:
- Report all KB counts as 0
- Note: "Knowledge base is empty. Run `/db-orient` to start building it."

---

## Step 3: Format Cost Threshold

Format the cost threshold into a human-readable string:

| Connection type | Threshold field | Display format |
|-----------------|-----------------|----------------|
| SQLite | (none) | `none (local database — no cost)` |
| Oracle | `warn_rows` | `{N:,} rows (EXPLAIN PLAN threshold)` |
| Athena | `warn_bytes` | `{N / 1024^3:.1f} GB ({N / 1024^4 * 5:.3f} USD/query at current pricing)` |
| Snowflake | `warn_rows` | `{N:,} rows` |

---

## Step 4: Output

Print the status block in this exact format:

```
DB Analyst Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connection    : {display_name} ({type})
Scope         : {schema_scope list joined by ", " | "all accessible schemas" | path for SQLite}
Read-only     : {Yes | No — override active if read_only is false}
Cost threshold: {formatted threshold string}

Knowledge Base
  Schemas with overview : {N}
  Tables documented     : {N}
  Active gotchas        : {N}
  Open questions        : {N}

Framework
  Skills  : db-dialect, db-cost-check, db-introspect, db-sample,
            db-orient, db-explain, db-status
  Hooks   : db-safety (pre-tool-use), db-cost-gate (pre-tool-use)

Run /db-orient to start exploring, or /db-explain {table} for a specific table.
```

---

## Annotated Examples

### Example 1 — SQLite demo, empty KB

```
DB Analyst Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connection    : DB Analyst Demo Database (sqlite)
Scope         : ./demo/demo.db
Read-only     : Yes
Cost threshold: none (local database — no cost)

Knowledge Base
  Schemas with overview : 0
  Tables documented     : 0
  Active gotchas        : 0
  Open questions        : 0

Framework
  Skills  : db-dialect, db-cost-check, db-introspect, db-sample,
            db-orient, db-explain, db-status
  Hooks   : db-safety (pre-tool-use), db-cost-gate (pre-tool-use)

Run /db-orient to start exploring, or /db-explain {table} for a specific table.
```

### Example 2 — Oracle production, populated KB

```
DB Analyst Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connection    : Production Oracle (oracle)
Scope         : SALES, HR
Read-only     : Yes
Cost threshold: 5,000,000 rows (EXPLAIN PLAN threshold)

Knowledge Base
  Schemas with overview : 2
  Tables documented     : 14
  Active gotchas        : 7
  Open questions        : 3

Framework
  Skills  : db-dialect, db-cost-check, db-introspect, db-sample,
            db-orient, db-explain, db-status
  Hooks   : db-safety (pre-tool-use), db-cost-gate (pre-tool-use)

Run /db-orient to start exploring, or /db-explain {table} for a specific table.
```

### Example 3 — Athena with threshold

```
DB Analyst Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connection    : Analytics Athena (athena)
Scope         : analytics
Read-only     : Yes
Cost threshold: 1.0 GB (~$0.005 USD/query at current pricing)

Knowledge Base
  Schemas with overview : 1
  Tables documented     : 6
  Active gotchas        : 2
  Open questions        : 1

Framework
  Skills  : db-dialect, db-cost-check, db-introspect, db-sample,
            db-orient, db-explain, db-status
  Hooks   : db-safety (pre-tool-use), db-cost-gate (pre-tool-use)

Run /db-orient to start exploring, or /db-explain {table} for a specific table.
```

---

## Notes

- This command is intentionally lightweight — it should return in under 2 seconds
- It does not verify that the database connection is live; it only reads configuration files
- If the user asks "am I connected?" in a broader sense (can I actually query?), suggest running a trivial introspection query like `SELECT COUNT(*) FROM sqlite_master` (SQLite) to confirm connectivity
- Do not surface the full content of KB files — just the counts; the analyst can read the files directly if they want details
