---
name: db-sample
description: This skill should be used when representative rows are needed to understand a table's data shape, detect soft-delete patterns, identify snapshot columns, or spot high null rates — typically as a step within /db-orient, /db-explain, /db-document, or /db-gotchas. Always uses row-limiting syntax to avoid full scans.
user-invocable: false
---

# DB Sample Skill

Use this skill to retrieve a small, representative sample of rows from a table plus a
value summary per column. It is designed to be fast and safe: it never performs full
table scans, and it respects the active connection's cost thresholds.

---

## Inputs

- **Table name** — required
- **Row count** — optional, default 10 (max 50 for safety)
- **Column list** — optional; if omitted, sample all columns (`SELECT *`)

---

## Step 1: Determine Dialect, Transport, and Row-Limit Syntax

Read `.claude/db-connections/active.yaml` to identify:
- `type` — the database dialect
- `transport` — `direct` (default) or `mcp`
- `mcp_server` — the MCP server name (when `transport: mcp`); forms tool names `mcp__{mcp_server}__{tool}`

Use the correct row-limiting clause for that dialect:

| Dialect | Row-limit syntax |
|---------|-----------------|
| SQLite  | `SELECT * FROM {table} LIMIT {n}` |
| Oracle (12c+) | `SELECT * FROM {schema}.{table} FETCH FIRST {n} ROWS ONLY` |
| Oracle (pre-12c) | `SELECT * FROM {schema}.{table} WHERE ROWNUM <= {n}` |
| Athena (Presto) | `SELECT * FROM {database}.{table} LIMIT {n}` |
| Snowflake | `SELECT * FROM {schema}.{table} LIMIT {n}` |

When a column list is provided:
```sql
SELECT {col1}, {col2}, {col3} FROM {table} LIMIT {n}
```

For Athena tables with known partition columns (from `db-introspect` output): include the
partition column in the SELECT and note it in the summary. Do NOT add a WHERE filter on
the partition key for samples — the goal is to see representative data across partitions.

---

## Step 2: Cost Check

- **SQLite:** No cost check needed. Run the query freely.
- **Oracle / Athena:** Sampling queries with `LIMIT N` or `FETCH FIRST N ROWS ONLY` are
  inherently cheap. Still route through the `db-cost-gate` hook path (it is in the hook
  chain regardless). If the gate fires, note that this is a bounded sample query and the
  actual scan will be minimal.
- **Snowflake / Salesforce:** Not yet implemented — note this and ask the user if they
  want to proceed with a manual query.

---

## Step 3: Run Sample Query

**If `transport: mcp`** — call the MCP server:

```
mcp__{mcp_server}__read_query  {"query": "SELECT {columns} FROM {table} LIMIT {n}"}
```

For value summary stats (Step 4), also use `read_query` with the appropriate aggregate SELECT.

**If `transport: direct`** — execute via Bash using the dialect-appropriate syntax from Step 1.
For Athena (`type: athena`), always use the wrapper script:
```bash
python scripts/athena_connect.py --query "SELECT {columns} FROM {database}.{table} LIMIT {n}" --format csv
```

In both cases, if the query fails:
- Permission error: Report "⚠️ Cannot sample {table} — permission denied"
- Table not found: Report "⚠️ Table {table} does not exist in the active schema"
- Other error: Report the error message and suggest the user verify the table name

---

## Step 4: Run Value Summary

After retrieving sample rows, run per-column statistics. Adapt queries per dialect.

### SQLite — per column (run as a single query)

```sql
SELECT
  COUNT(*)                        AS total_rows,
  COUNT({column})                 AS non_null_count,
  COUNT(DISTINCT {column})        AS distinct_count,
  MIN({column})                   AS min_val,
  MAX({column})                   AS max_val
FROM {table_name};
```

Run one such query per column, or combine into a single wide SELECT:

```sql
SELECT
  COUNT(*) AS total_rows,
  -- repeat per column:
  COUNT({col1}) AS {col1}_non_null,
  COUNT(DISTINCT {col1}) AS {col1}_distinct,
  MIN({col1}) AS {col1}_min,
  MAX({col1}) AS {col1}_max
  -- ...
FROM {table_name};
```

Use judgment about column count — for wide tables (>20 cols), run stats only on columns
that appear in the sample or that the user mentioned.

### Oracle — per column

```sql
SELECT
  COUNT(*) AS total_rows,
  COUNT({column}) AS non_null_count,
  COUNT(DISTINCT {column}) AS distinct_count,
  MIN({column}) AS min_val,
  MAX({column}) AS max_val
FROM {SCHEMA}.{TABLE};
```

Note: `MIN`/`MAX` on CLOB columns will error. Skip stats for LOB-typed columns; note them as "LOB — stats not available."

### Athena — per column

```sql
SELECT
  COUNT(*) AS total_rows,
  COUNT({column}) AS non_null_count,
  approx_distinct({column}) AS approx_distinct,
  MIN({column}) AS min_val,
  MAX({column}) AS max_val
FROM {database}.{table};
```

Use `approx_distinct()` instead of `COUNT(DISTINCT)` for Athena — it is much cheaper on large tables. Label the output column `approx_distinct` to be honest about the approximation.

---

## Step 5: Format Output

```
## Sample: {SCHEMA}.{TABLE_NAME} ({n} rows)

| {col1} | {col2} | {col3} | ... |
|--------|--------|--------|-----|
| val    | val    | val    | ... |
| val    | val    | val    | ... |
...

### Value Summary (full table)

| Column | Type | Non-null | Distinct | Min | Max |
|--------|------|----------|----------|-----|-----|
| col1   | INTEGER | 1,000 / 1,000 | 1,000 | 1 | 9,999 |
| col2   | TEXT | 998 / 1,000 | 45 | "Aaron" | "Zoe" |
| col3   | TEXT | 0 / 1,000 | 0 | — | — | ← all null
```

**Formatting conventions:**
- Show `{non_null} / {total}` for the Non-null column so null rate is immediately visible
- For columns where all values are NULL, show `← all null` as a note
- For columns where all values are identical, show `← constant` as a note
- Format large numbers with commas (1,000 not 1000)
- Truncate very long string values in the sample grid (max 40 chars, add `…`)

---

## Notes

- The row limit is applied at the database level, not in post-processing — never fetch more rows than needed
- Do not cache sample results; always fetch fresh data
- If the table has 0 rows, report "Table is empty" and skip the value summary
- This skill does not explain the data — use `db-explain` to interpret what the sample means
