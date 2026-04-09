---
name: db-dialect
description: This skill should be used when performing any database work that requires dialect-specific SQL syntax, metadata queries, or cost estimation — including introspecting tables, writing queries, or checking EXPLAIN output. Apply it whenever the active connection type (sqlite, oracle, athena, snowflake, salesforce) determines which SQL forms to use.
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

## Snowflake

**Use when:** `type: snowflake` in active connection.

### Metadata Queries

```sql
-- List all databases accessible to the current role
SHOW DATABASES;

-- List schemas in a database
SHOW SCHEMAS IN DATABASE {database};

-- List tables in a schema
SHOW TABLES IN SCHEMA {database}.{schema};

-- List views in a schema
SHOW VIEWS IN SCHEMA {database}.{schema};

-- Detailed column info
SELECT column_name, data_type, is_nullable, column_default, ordinal_position,
       comment
FROM {database}.information_schema.columns
WHERE table_schema = '{SCHEMA}' AND table_name = '{TABLE}'
ORDER BY ordinal_position;

-- All tables in a schema with row counts
SELECT table_name, table_type, row_count, bytes, created, last_altered, comment
FROM {database}.information_schema.tables
WHERE table_schema = '{SCHEMA}'
ORDER BY table_name;

-- Primary keys (Snowflake tracks but does not enforce)
SHOW PRIMARY KEYS IN TABLE {database}.{schema}.{table};

-- Foreign keys (tracked, not enforced)
SHOW IMPORTED KEYS IN TABLE {database}.{schema}.{table};

-- Sample rows
SELECT * FROM {database}.{schema}.{table} LIMIT 10;

-- Column stats
SELECT
  COUNT(*) AS total_rows,
  COUNT({column}) AS non_null_count,
  COUNT(DISTINCT {column}) AS distinct_count,
  MIN({column}) AS min_val,
  MAX({column}) AS max_val
FROM {database}.{schema}.{table};
```

### Cost Signal — EXPLAIN

```sql
EXPLAIN {your_query};
```

Parse the EXPLAIN output for `bytesAssigned` or `partitionsTotal`. Snowflake does not report
bytes scanned directly in EXPLAIN text — use `row_count` from `information_schema.tables` and
the `warn_rows` threshold as a proxy. For precise cost monitoring, use Snowflake's
`QUERY_HISTORY` view after execution:

```sql
SELECT query_text, bytes_scanned, credits_used_cloud_services, total_elapsed_time
FROM snowflake.account_usage.query_history
WHERE start_time >= DATEADD('hour', -1, CURRENT_TIMESTAMP())
ORDER BY start_time DESC
LIMIT 10;
```

**Threshold check:** If `information_schema.tables.row_count > warn_rows`, surface a warning
before executing the full query.

### Notes
- Always qualify table names: `{database}.{schema}.{table}`
- `INFORMATION_SCHEMA` is per-database; `SNOWFLAKE.ACCOUNT_USAGE` is org-wide (requires `ACCOUNTADMIN` or specific grants)
- `SHOW` commands return metadata without scanning data — use freely for introspection
- Snowflake is case-insensitive for identifiers by default; quoted identifiers are case-sensitive
- Date/time functions: `CURRENT_TIMESTAMP()`, `DATEADD('day', N, col)`, `DATEDIFF('day', a, b)`, `DATE_TRUNC('month', col)`
- Semi-structured data: `col:field::type` (colon path), `FLATTEN(INPUT => col)`, `PARSE_JSON(str)`
- Approximate aggregates: `APPROX_COUNT_DISTINCT(col)`, `APPROX_PERCENTILE(col, 0.5)`
- Sampling: `SELECT * FROM {table} SAMPLE (1)` — returns ~1% of rows without full scan
- Warehouse size affects compute cost; always check the active warehouse before running heavy queries

---

## Salesforce / SOQL

**Use when:** `type: salesforce` in active connection.

> Salesforce uses **SOQL** (Salesforce Object Query Language), not SQL. Standard SQL syntax
> will not work. Apply the `db-soql` skill for full SOQL construction, relationship traversal,
> and governor limit guidance. This section provides the dialect detection and quick reference
> needed to route correctly.

### Metadata — describeSObject (not INFORMATION_SCHEMA)

Salesforce metadata is accessed via the REST API, not SQL system tables.

```
-- List all queryable objects
GET {instance_url}/services/data/v{api_version}/sobjects/

-- Describe a specific object (fields, types, relationships, picklist values)
GET {instance_url}/services/data/v{api_version}/sobjects/{ObjectName}/describe/
```

Key fields from describe response:
- `fields[].name` — API name (use in SOQL)
- `fields[].type` — data type (string, reference, picklist, double, datetime, etc.)
- `fields[].relationshipName` — use this in child-to-parent dot notation
- `fields[].referenceTo` — which object(s) a reference field points to
- `fields[].picklistValues` — valid values for picklist fields
- `childRelationships[].relationshipName` — use in parent-to-child subqueries
- `childRelationships[].childSObject` — the related child object name

### Quick SOQL Reference

```soql
-- Basic pattern (SELECT * is not valid; always list fields)
SELECT Id, Name, CreatedDate
FROM Account
WHERE IsDeleted = false
LIMIT 200

-- Row count estimate (always run before large queries)
SELECT COUNT()
FROM {Object}
WHERE IsDeleted = false AND {your_filters}

-- Child-to-parent relationship (dot notation)
SELECT Id, Name, Account.Name, Account.Industry
FROM Contact
WHERE IsDeleted = false

-- Parent-to-child subquery
SELECT Id, Name, (SELECT Id, Subject FROM Cases WHERE IsDeleted = false)
FROM Account
WHERE IsDeleted = false
LIMIT 50
```

### Cost Signal — Record Count

SOQL has no EXPLAIN. Use a `SELECT COUNT()` query with the same WHERE clause to estimate
rows before running the full query. Compare against `warn_records` in `active.yaml`.

Governor limits:
- Synchronous queries: **50,000 rows max** per transaction
- Queries per transaction: 100
- Non-selective queries on large objects (> 200k records) will time out

### Always-On Rules for Salesforce
- `IsDeleted = false` — include in every query unless explicitly querying the recycle bin
- `SELECT *` is a syntax error — always list field names explicitly
- Relationship names (for joins) come from `describeSObject`, not field names
- Picklist values must match exactly — wrong values return 0 rows silently
- For complex SOQL: use the `db-soql` skill

---

## MCP Tool Reference

When `transport: mcp` is set in `active.yaml`, skills call MCP server tools instead of
executing Bash/Python. The tool name format is:

```
mcp__{mcp_server}__{tool_name}
```

where `mcp_server` is the value from `active.yaml` (e.g. `sqlite`, `snowflake`).

| Dialect | Enumerate tables | Describe table schema | Execute read query |
|---------|-----------------|----------------------|-------------------|
| **sqlite** | `list_tables` | `describe_table` | `read_query` |
| **snowflake** | `list_schemas`, `list_tables` | `describe_table` | `execute_query` |
| oracle | no official MCP server — use `transport: direct` | — | — |
| athena | no official MCP server — use `transport: direct` | — | — |
| salesforce | REST API via Bash — use `transport: direct` | — | — |

**SQLite MCP tool signatures:**
- `list_tables` — no parameters; returns all table names
- `describe_table {"table_name": "{table}"}` — returns columns with types and nullability
- `read_query {"query": "{sql}"}` — executes a SELECT and returns rows

**Snowflake MCP tool signatures (official Snowflake MCP server):**
- `list_schemas` / `list_tables` — enumerate available objects
- `describe_table {"table_name": "{db}.{schema}.{table}"}` — column metadata
- `execute_query {"query": "{sql}"}` — executes a read query

Note: MCP tool calls are intercepted by the `db-safety` and `db-cost-gate` hooks exactly
like Bash calls — no extra configuration needed.

---

## Dialect Detection

When starting any database work, Claude should:

1. Read `.claude/db-connections/active.yaml`
2. Find the connection named in `active:`
3. Check its `type:` field
4. Check `transport:` (default: `direct`) and `mcp_server:` if transport is `mcp`
5. Apply the corresponding dialect section from this skill
6. Load `cost_thresholds` for use in cost checks

If `active.yaml` does not exist, prompt the user to set up their connection config by copying `templates/connections.example.yaml` to `.claude/db-connections/active.yaml`.
