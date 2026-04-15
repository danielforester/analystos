---
name: db-introspect
description: This skill should be used whenever schema metadata is needed before explaining, orienting, querying, or documenting a database object. Apply it to extract columns, types, PKs, FKs, indexes, comments, and row counts from any table or schema. It is the foundation step for /db-orient, /db-explain, /db-query, and /db-document.
user-invocable: false
---

# DB Introspect Skill

Use this skill to extract structured metadata from a database. It is the foundation for
`/db-orient` and `/db-explain` — always run introspection before synthesizing explanations
or orientation summaries.

---

## Inputs

- **Target:** A table name, view name, or nothing (schema-wide introspection)
- **Schema:** An explicit schema/owner name to introspect. When provided, overrides `schema_scope` for this call.
- **Scope:** From the active connection's `schema_scope` in `.claude/db-connections/active.yaml` (used when no explicit schema is passed)

### Schema Discovery Mode

When called with **no target table** and **no explicit schema**, and the connection has
more than one schema in scope (or `schema_scope` is unset), do **not** auto-expand into
full per-table introspection. Instead:

1. Run the schema-list query for the active dialect (see below)
2. Return the results as a schema inventory table (schema name, table count, view count)
3. Stop — let the caller (e.g. `/db-orient`) decide which schema to drill into

This prevents an accidental full introspection of a wide-open Oracle or Snowflake
environment with dozens of schemas.

**Schema-list queries by dialect:**

```sql
-- Oracle: schemas within schema_scope (omit WHERE clause if scope is unset)
SELECT owner AS schema_name,
       COUNT(*) AS table_count
FROM all_tables
WHERE owner IN ({schema_scope_list})
GROUP BY owner
ORDER BY owner;

-- Snowflake
SELECT table_schema AS schema_name,
       COUNT(*) AS table_count
FROM information_schema.tables
WHERE table_catalog = CURRENT_DATABASE()
  AND table_type = 'BASE TABLE'
GROUP BY table_schema
ORDER BY table_schema;

-- Athena
SELECT table_schema AS schema_name,
       COUNT(*) AS table_count
FROM information_schema.tables
WHERE table_type = 'BASE TABLE'
GROUP BY table_schema
ORDER BY table_schema;
```

SQLite has a single implied schema (the database file) — schema discovery mode does
not apply. Proceed directly to table introspection.

**Schema discovery output format:**

```
## Available Schemas

| Schema | Tables |
|--------|--------|
| FINANCE | 42 |
| HR | 18 |
| SALES | 67 |

3 schemas in scope. Pass a schema name to introspect a specific one.
```

If the scope is unset and the list is long (>10 schemas), add:
> "⚠️ `schema_scope` is not set in `active.yaml` — showing all accessible schemas.
> Consider narrowing `schema_scope` to your working set."

---

## Step 1: Load Connection Context

Read `.claude/db-connections/active.yaml`. Identify:
- `type` — the database dialect (sqlite, oracle, athena, snowflake, salesforce)
- `transport` — `direct` (default) or `mcp`. Determines how queries are executed in Step 2.
- `mcp_server` — the MCP server name (required when `transport: mcp`). Used to form tool names: `mcp__{mcp_server}__{tool}`.
- `schema_scope` — the schemas or databases in scope (Oracle: list of owner names; Athena: database name; SQLite: N/A)
- Connection-specific fields needed to qualify table names

Apply the `db-dialect` skill to determine the correct metadata queries for this connection type.

---

## Step 2: Run Metadata Queries

### SQLite

**If `transport: mcp`** — use MCP tools instead of Bash:

```
mcp__{mcp_server}__list_tables
  → returns all table names in the database

mcp__{mcp_server}__describe_table  {"table_name": "{table}"}
  → returns columns, types, and nullability (replaces PRAGMA table_info)

mcp__{mcp_server}__read_query  {"query": "SELECT COUNT(*) AS row_count FROM {table}"}
  → for row counts (run per table)
```

FKs and indexes are not available via the standard SQLite MCP server — note "FK/index data unavailable via MCP" in the output and suggest the user switch to `transport: direct` if this detail is needed.

**If `transport: direct`** — run via Bash:

```sql
-- All tables and views
SELECT name, type FROM sqlite_master
WHERE type IN ('table', 'view')
ORDER BY type, name;

-- Per table: columns
PRAGMA table_info({table_name});
-- Returns: cid, name, type, notnull, dflt_value, pk

-- Per table: foreign keys
PRAGMA foreign_key_list({table_name});
-- Returns: id, seq, table (ref_table), from (fk_col), to (ref_col)

-- Per table: indexes
PRAGMA index_list({table_name});
-- Then per index: PRAGMA index_info({index_name});

-- Row count
SELECT COUNT(*) AS row_count FROM {table_name};
```

SQLite has no native table comments. Column type strings are loose (affinity-based) — note this in output.

---

### Oracle

**If `transport: direct`** — execute each query via the wrapper script:

```bash
python .claude/scripts/oracle_connect.py --query "{sql}" --format csv
```

Metadata queries:

```sql
-- All tables in scope
SELECT owner, table_name, num_rows, last_analyzed
FROM all_tables
WHERE owner IN ({schema_scope_list})
ORDER BY owner, table_name;

-- All views in scope
SELECT owner, view_name FROM all_views
WHERE owner IN ({schema_scope_list})
ORDER BY owner, view_name;

-- Columns for a specific table
SELECT column_name, data_type, data_length, data_precision, data_scale,
       nullable, data_default, column_id
FROM all_columns
WHERE owner = '{SCHEMA}' AND table_name = '{TABLE}'
ORDER BY column_id;

-- Table comment
SELECT comments FROM all_tab_comments
WHERE owner = '{SCHEMA}' AND table_name = '{TABLE}';

-- Column comments
SELECT column_name, comments FROM all_col_comments
WHERE owner = '{SCHEMA}' AND table_name = '{TABLE}'
ORDER BY column_name;

-- Primary key columns
SELECT cols.column_name, cols.position
FROM all_constraints cons
JOIN all_cons_columns cols
  ON cons.constraint_name = cols.constraint_name
  AND cons.owner = cols.owner
WHERE cons.constraint_type = 'P'
  AND cons.owner = '{SCHEMA}'
  AND cons.table_name = '{TABLE}'
ORDER BY cols.position;

-- Foreign keys
SELECT a.column_name AS fk_column,
       c_pk.owner AS ref_schema,
       c_pk.table_name AS ref_table,
       b.column_name AS ref_column
FROM all_cons_columns a
JOIN all_constraints c
  ON a.constraint_name = c.constraint_name AND a.owner = c.owner
JOIN all_constraints c_pk
  ON c.r_constraint_name = c_pk.constraint_name
JOIN all_cons_columns b
  ON c_pk.constraint_name = b.constraint_name
WHERE c.constraint_type = 'R'
  AND a.owner = '{SCHEMA}'
  AND a.table_name = '{TABLE}';

-- Indexes
SELECT index_name, index_type, uniqueness, status
FROM all_indexes
WHERE owner = '{SCHEMA}' AND table_name = '{TABLE}';

SELECT index_name, column_name, column_position
FROM all_ind_columns
WHERE index_owner = '{SCHEMA}' AND table_name = '{TABLE}'
ORDER BY index_name, column_position;
```

Note: `num_rows` in `all_tables` reflects the last ANALYZE run and may be stale. State this in output.

### AWS Athena (Presto/Trino)

**If `transport: direct`** — execute each query via the wrapper script:

```bash
python .claude/scripts/athena_connect.py --query "{sql}" --format csv
```

Metadata queries:

```sql
-- All tables in the active database
SELECT table_name, table_type
FROM information_schema.tables
WHERE table_schema = '{database}'
ORDER BY table_name;

-- Columns for a specific table
SELECT column_name, data_type, is_nullable, ordinal_position
FROM information_schema.columns
WHERE table_schema = '{database}' AND table_name = '{table}'
ORDER BY ordinal_position;

-- Table properties including partition keys and storage format
SHOW CREATE TABLE {database}.{table};

-- Partition keys (if any)
SHOW PARTITIONS {database}.{table};
```

Athena has no column comments or FK constraints in the catalog. Note this in output. Partition columns are critical for cost-aware querying — always extract and highlight them.

### Snowflake

**If `transport: direct`** — execute each query via the wrapper script:

```bash
python .claude/scripts/snowflake_connect.py --query "{sql}" --format csv
```

Metadata queries:

```sql
-- All tables in a schema with row counts
SELECT table_name, table_type, row_count, bytes, comment
FROM {database}.information_schema.tables
WHERE table_schema = '{SCHEMA}'
ORDER BY table_name;

-- Columns for a specific table (includes column comments)
SELECT column_name, data_type, is_nullable, column_default, ordinal_position, comment
FROM {database}.information_schema.columns
WHERE table_schema = '{SCHEMA}' AND table_name = '{TABLE}'
ORDER BY ordinal_position;

-- Primary keys (Snowflake tracks but does not enforce)
SHOW PRIMARY KEYS IN TABLE {database}.{schema}.{table};

-- Foreign keys (tracked, not enforced)
SHOW IMPORTED KEYS IN TABLE {database}.{schema}.{table};

-- Clustering keys (affects micro-partition pruning and scan cost)
SHOW TABLES LIKE '{table}' IN SCHEMA {database}.{schema};
```

Note: `row_count` from `information_schema.tables` is maintained automatically by Snowflake and is generally accurate. PKs and FKs are tracked as metadata but not enforced — note this in output.

---

## Step 3: Format Output

Produce one structured block per table or view. Use this format:

```
## {SCHEMA}.{TABLE_NAME}  [{table | view}]
**Rows (approx):** {row_count or "unknown"} {(stale — last analyzed YYYY-MM-DD) if Oracle}
**Comment:** {table comment, or "none"}

| Column | Type | Nullable | PK | FK | Comment |
|--------|------|----------|----|-----|---------|
| col1   | INTEGER | No | ✓ |  | Primary key |
| col2   | VARCHAR(100) | No | | | Customer name |
| col3   | INTEGER | Yes | | → other_table.id | Foreign key to other_table |

**Foreign Keys:**
- `{fk_column}` → `{ref_schema}.{ref_table}.{ref_column}`

**Indexes:**
- `{index_name}` on (`{col1}`, `{col2}`) [{UNIQUE | NON-UNIQUE}]

**Partition Keys (Athena only):**
- `{partition_column}` — always filter on this column to control scan cost
```

For schema-wide introspection, output all tables in order, then add a summary:

```
## Introspection Summary
Tables: {N}
Views: {N}
Total columns: {N}
Tables with FK relationships: {N}
```

---

## Step 4: Error Handling

- If a table is inaccessible (permissions, does not exist): note "⚠️ {TABLE}: inaccessible — skipping" and continue
- If row count query fails (e.g., large table where COUNT is blocked): use `num_rows` from catalog if available, otherwise "unknown"
- If introspection returns 0 tables: tell the user the schema appears empty or the scope may be misconfigured, and show the `schema_scope` value from `active.yaml`

---

## Notes

- Always qualify table names with schema/owner when the dialect requires it
- For SQLite, note that type affinity (not strict typing) means `INTEGER` columns may store any value
- Do not run EXPLAIN or cost checks for introspection queries — they are metadata reads and are cheap
- This skill does not sample data — use `db-sample` for representative rows
