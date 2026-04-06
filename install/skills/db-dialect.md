---
name: db-dialect
description: Dialect-aware metadata extraction and syntax reference for Oracle, Athena, and SQLite. Use this skill whenever you need to introspect a database, build dialect-correct SQL, or understand how to estimate query cost.
---

# DB Dialect Skill

When working with a database, always consult the active connection type from `.claude/db-connections/active.yaml` and apply the appropriate dialect below.

---

## SQLite

**Use when:** `type: sqlite` in active connection.

### Metadata Queries

```sql
-- List all tables and views
SELECT name, type FROM sqlite_master
WHERE type IN ('table', 'view')
ORDER BY type, name;

-- Table structure (columns, types, nullability, defaults)
PRAGMA table_info({table_name});
-- Returns: cid, name, type, notnull, dflt_value, pk

-- Foreign keys
PRAGMA foreign_key_list({table_name});
-- Returns: id, seq, table, from, to, on_update, on_delete, match

-- Indexes on a table
PRAGMA index_list({table_name});
-- Then for each index: PRAGMA index_info({index_name});

-- Row count estimate
SELECT COUNT(*) FROM {table_name};

-- Sample rows (safe, small)
SELECT * FROM {table_name} LIMIT 10;

-- All column stats (SQLite has no built-in ANALYZE output for columns;
-- use COUNT, COUNT DISTINCT, MIN, MAX per column)
SELECT
  COUNT(*) AS total_rows,
  COUNT({column}) AS non_null_count,
  COUNT(DISTINCT {column}) AS distinct_count,
  MIN({column}) AS min_val,
  MAX({column}) AS max_val
FROM {table_name};
```

### Cost Signal
SQLite is a local file database. There is **no network or compute cost**. Do not apply cost thresholds. Proceed with queries freely, but still apply the read-only safety check.

### Writable PRAGMAs to Block
The safety hook blocks these by default — do not suggest them:
- `PRAGMA journal_mode = ...`
- `PRAGMA foreign_keys = ON/OFF` (state change)
- `ATTACH DATABASE ...`
- Any `PRAGMA` that takes an assignment (`=`)

---

## Oracle

**Use when:** `type: oracle` in active connection.

### Metadata Queries

```sql
-- Tables visible to the current user
SELECT owner, table_name, num_rows, last_analyzed
FROM all_tables
WHERE owner IN ({schema_scope})   -- from schema_scope in connections.yaml
ORDER BY owner, table_name;

-- Columns for a table
SELECT column_name, data_type, data_length, data_precision, data_scale,
       nullable, data_default, column_id
FROM all_columns
WHERE owner = '{SCHEMA}' AND table_name = '{TABLE}'
ORDER BY column_id;

-- Table and column comments
SELECT comments FROM all_tab_comments
WHERE owner = '{SCHEMA}' AND table_name = '{TABLE}';

SELECT column_name, comments FROM all_col_comments
WHERE owner = '{SCHEMA}' AND table_name = '{TABLE}';

-- Views
SELECT owner, view_name, text_length
FROM all_views
WHERE owner IN ({schema_scope})
ORDER BY owner, view_name;

-- Primary keys
SELECT cols.column_name, cols.position
FROM all_constraints cons
JOIN all_cons_columns cols
  ON cons.constraint_name = cols.constraint_name
  AND cons.owner = cols.owner
WHERE cons.constraint_type = 'P'
  AND cons.owner = '{SCHEMA}'
  AND cons.table_name = '{TABLE}';

-- Foreign keys
SELECT a.column_name AS fk_column,
       c_pk.owner AS ref_schema,
       c_pk.table_name AS ref_table,
       b.column_name AS ref_column
FROM all_cons_columns a
JOIN all_constraints c ON a.constraint_name = c.constraint_name AND a.owner = c.owner
JOIN all_constraints c_pk ON c.r_constraint_name = c_pk.constraint_name
JOIN all_cons_columns b ON c_pk.constraint_name = b.constraint_name
WHERE c.constraint_type = 'R'
  AND a.owner = '{SCHEMA}'
  AND a.table_name = '{TABLE}';

-- Sample rows
SELECT * FROM {SCHEMA}.{TABLE} WHERE ROWNUM <= 10;
```

### Cost Signal — EXPLAIN PLAN

```sql
-- Step 1: Generate plan (does not execute the query)
EXPLAIN PLAN FOR
{your_query};

-- Step 2: Read the plan
SELECT operation, options, object_name, cardinality, bytes, cost
FROM plan_table
WHERE plan_id = (SELECT MAX(plan_id) FROM plan_table)
ORDER BY id;

-- The root row (id=0) cardinality is the total row estimate.
-- Compare against cost_thresholds.warn_rows from connections.yaml.
```

**Threshold check:** If root `cardinality > warn_rows`, surface a warning and ask for confirmation before running the full query.

### Notes
- Always qualify table names with the schema owner: `{SCHEMA}.{TABLE}`
- `ALL_*` views show objects the current user can access; `DBA_*` require elevated privileges
- `num_rows` in `all_tables` reflects the last `ANALYZE` / `DBMS_STATS` run — may be stale
- Date literals: `DATE '2024-01-15'` or `TO_DATE('2024-01-15', 'YYYY-MM-DD')`
- Use `FETCH FIRST n ROWS ONLY` (Oracle 12c+) or `ROWNUM <= n` for row limits

---

## AWS Athena (Presto/Trino dialect)

**Use when:** `type: athena` in active connection.

### Metadata Queries

```sql
-- Databases (Glue catalogs)
SHOW DATABASES;

-- Tables in a database
SHOW TABLES IN {database};

-- Table structure
DESCRIBE {database}.{table};

-- Detailed column info via information_schema
SELECT column_name, data_type, is_nullable, column_default, ordinal_position
FROM information_schema.columns
WHERE table_schema = '{database}' AND table_name = '{table}'
ORDER BY ordinal_position;

-- All tables in a database
SELECT table_name, table_type
FROM information_schema.tables
WHERE table_schema = '{database}'
ORDER BY table_name;

-- Table properties (location, format, partition keys)
SHOW CREATE TABLE {database}.{table};

-- Partitions
SHOW PARTITIONS {database}.{table};

-- Sample rows (always include partition filter if table is partitioned)
SELECT * FROM {database}.{table} LIMIT 10;
```

### Cost Signal — EXPLAIN

```sql
EXPLAIN {your_query};
```

Parse the output for `Est. Output rows` and estimated data read. The Athena query engine reports estimated bytes in the EXPLAIN output text — look for patterns like `rows = X` and data size estimates.

**Threshold check:** If estimated bytes scanned > `warn_bytes` from connections.yaml, surface a warning with the estimate formatted as human-readable (e.g., "~2.3 GB") before running.

**Athena cost reference:** ~$5 per TB scanned. Use partitioning and column pruning aggressively.

### Notes
- Athena uses **Presto/Trino SQL** — not ANSI or Oracle SQL
- String concatenation: `||` or `concat(a, b)`
- Date functions: `date_trunc('month', col)`, `date_diff('day', a, b)`, `date_parse(str, fmt)`
- Always filter on partition columns when available to minimize scanned data
- Use `SELECT col1, col2` instead of `SELECT *` to reduce bytes scanned on columnar formats
- Array/map types are common: `UNNEST(array_col)`, `map_keys(map_col)`, `element_at(map_col, key)`
- Approximate aggregates: `approx_distinct(col)`, `approx_percentile(col, 0.5)`

---

## Snowflake (Stub — Sprint 3+)

**Use when:** `type: snowflake` in active connection.

> Snowflake dialect support is not yet implemented. When a user connects to Snowflake:
> 1. Acknowledge the connection type
> 2. Inform the user that full Snowflake support is planned for Sprint 3
> 3. Offer to proceed with manual SQL using Snowflake's `INFORMATION_SCHEMA` if they want to work ahead

---

## Salesforce / SOQL (Stub — Sprint 3+)

**Use when:** `type: salesforce` in active connection.

> Salesforce SOQL support is not yet implemented. When a user connects to Salesforce:
> 1. Acknowledge the connection type
> 2. Inform the user that Salesforce support (including `/db-soql`) is planned for Sprint 3
> 3. Note that Salesforce uses SOQL, not SQL — standard SQL queries will not work

---

## Dialect Detection

When starting any database work, Claude should:

1. Read `.claude/db-connections/active.yaml`
2. Find the connection named in `active:`
3. Check its `type:` field
4. Apply the corresponding dialect section from this skill
5. Load `cost_thresholds` for use in cost checks

If `active.yaml` does not exist, prompt the user to set up their connection config by copying `templates/connections.example.yaml` to `.claude/db-connections/active.yaml`.
