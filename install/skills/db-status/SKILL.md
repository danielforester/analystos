---
name: db-status
description: Show the current DB Analyst session state — active connection, schema scope, cost thresholds, and knowledge base inventory. No database calls required. Run /db-status at any time to confirm what is loaded and ready.
user-invocable: true
allowed-tools:
  - Read
---

# /db-status

**Invocation:** `/db-status`

Use this command to get an instant snapshot of the current session state: what database
is connected, what knowledge base entries are loaded, and what safety settings are active.
This command never makes database calls — it only reads local files.

---

## Behavior

1. Read the active connection from `.claude/db-connections/active.yaml`
2. Scan the knowledge base at `db-knowledge/`
3. Report the session state in a compact, scannable format

---

## Step 1: Read Active Connection

Read `.claude/db-connections/active.yaml`.

Parse all entries in `connections[]` — these are all available connections. Find the one
whose `name` matches the `active:` field. Extract from the active connection:

| Field | Source |
|-------|--------|
| Display name | `display_name` |
| Type | `type` |
| Schema scope | `schema_scope` (Oracle), `database` (Athena), `sqlite.path` (SQLite), or "all accessible" |
| Read-only | `read_only` (true/false) |
| Transport | `transport` (default: `direct`) |
| MCP server | `mcp_server` (only when transport is `mcp`) |
| Cost threshold | `cost_thresholds.warn_rows` (Oracle) or `cost_thresholds.warn_bytes` (Athena) or null (SQLite) |

Also collect all connection names and types from `connections[]` for Step 4 output.

**If `active.yaml` does not exist:**

```
AnalystOS Status
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

Scan `db-knowledge/`. The structure is two levels deep: `db-knowledge/{connection-name}/{schema}/`.
Connection-level directories are any subdirectories not prefixed with `_`. Within each, schema
directories are also any subdirectories not prefixed with `_`.

Collect:

- **Schemas with overview:** Count `{connection-name}/{schema}/` directories that contain a `_schema-overview.md` file
- **Tables documented:** Count all `{table}.md` files at the schema level (exclude files starting with `_`)
- **Active gotchas:** Count `## ⚠️` or `### ⚠️` headings in all `_gotchas.md` files (root, connection-level, and schema-level)
- **Open questions:** Count unchecked `- [ ]` items in all `_open-questions.md` files (root and all subdirs)

If `db-knowledge/` does not exist or is empty:
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
AnalystOS Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connection    : {display_name} ({type})
Scope         : {schema_scope list joined by ", " | "all accessible schemas" | path for SQLite}
Read-only     : {Yes | No — override active if read_only is false}
Transport     : {direct | mcp (server: {mcp_server})}
Cost threshold: {formatted threshold string}

Configured Connections
  {for each connection in connections[], one line:}
  ▶ {name}   {display_name}   {type}    ← active
    {name}   {display_name}   {type}
  Run /db-use <name> to switch.

Knowledge Base
  Schemas with overview : {N}
  Tables documented     : {N}
  Active gotchas        : {N}
  Open questions        : {N}

Framework
  Skills  : db-dialect, db-cost-check, db-introspect, db-sample,
            db-orient, db-explain, db-status, db-use
  Hooks   : db-safety (pre-tool-use), db-cost-gate (pre-tool-use)

Run /db-orient to start exploring, or /db-explain {table} for a specific table.
```

If only one connection is configured, omit the "Configured Connections" block.

---

## Annotated Examples

### Example 1 — SQLite demo, single connection, empty KB

```
AnalystOS Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connection    : Demo Database (sqlite)
Scope         : ./demo/demo.db
Read-only     : Yes
Transport     : direct
Cost threshold: none (local database — no cost)

Knowledge Base
  Schemas with overview : 0
  Tables documented     : 0
  Active gotchas        : 0
  Open questions        : 0

Framework
  Skills  : db-dialect, db-cost-check, db-introspect, db-sample,
            db-orient, db-explain, db-status, db-use
  Hooks   : db-safety (pre-tool-use), db-cost-gate (pre-tool-use)

Run /db-orient to start exploring, or /db-explain {table} for a specific table.
```

### Example 2 — Multiple connections, Oracle active, populated KB

```
AnalystOS Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connection    : Production Oracle (oracle)
Scope         : SALES, HR
Read-only     : Yes
Transport     : direct
Cost threshold: 5,000,000 rows (EXPLAIN PLAN threshold)

Configured Connections
  ▶ oracle-prod    Production Oracle             oracle   ← active
    snowflake-dw   Data Warehouse (Snowflake)    snowflake
    demo           Demo Database                 sqlite
  Run /db-use <name> to switch.

Knowledge Base
  Schemas with overview : 2
  Tables documented     : 14
  Active gotchas        : 7
  Open questions        : 3

Framework
  Skills  : db-dialect, db-cost-check, db-introspect, db-sample,
            db-orient, db-explain, db-status, db-use
  Hooks   : db-safety (pre-tool-use), db-cost-gate (pre-tool-use)

Run /db-orient to start exploring, or /db-explain {table} for a specific table.
```

### Example 3 — Athena with threshold

```
AnalystOS Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connection    : Analytics Athena (athena)
Scope         : analytics
Read-only     : Yes
Transport     : direct
Cost threshold: 1.0 GB (~$0.005 USD/query at current pricing)

Configured Connections
  ▶ athena-analytics   Analytics Athena (us-east-1)   athena   ← active
    demo               Demo Database                  sqlite
  Run /db-use <name> to switch.

Knowledge Base
  Schemas with overview : 1
  Tables documented     : 6
  Active gotchas        : 2
  Open questions        : 1

Framework
  Skills  : db-dialect, db-cost-check, db-introspect, db-sample,
            db-orient, db-explain, db-status, db-use
  Hooks   : db-safety (pre-tool-use), db-cost-gate (pre-tool-use)

Run /db-orient to start exploring, or /db-explain {table} for a specific table.
```

---

## Notes

- This command is intentionally lightweight — it should return in under 2 seconds
- It does not verify that the database connection is live; it only reads configuration files
- If the user asks "am I connected?" in a broader sense (can I actually query?), suggest running a trivial introspection query like `SELECT COUNT(*) FROM sqlite_master` (SQLite) to confirm connectivity
- Do not surface the full content of KB files — just the counts; the analyst can read the files directly if they want details
