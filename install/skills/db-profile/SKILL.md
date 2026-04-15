---
name: db-profile
description: Statistical profile of a table or column. Reports row counts, null rates, distinct value counts, min/max, top-N values, and numeric distribution. Always runs through db-cost-gate before execution. Use /db-profile to understand the shape and health of a table or column before writing queries.
user-invocable: true
argument-hint: "<table_name> [column_name]"
allowed-tools:
  - Read
  - Bash
  - Write
---

# /db-profile

**Invocation:**
- `/db-profile {table_name}` — full table profile
- `/db-profile {table_name}.{column_name}` — single column deep-dive

Use this command when an analyst wants to understand the statistical shape of a table
or column: how many rows, what values appear, how sparse the data is, and whether
anything looks anomalous.

---

## Behavior Overview

1. Parse the target (table or column)
2. Run cost check via `db-cost-gate`
3. Execute profile queries
4. Format and present results
5. Flag anomalies
6. Offer to save anomaly flags to the knowledge base (table profiles only)

---

## Step 1: Parse the Target

| Input | Mode |
|-------|------|
| `sales_orders` | Table profile — stats for all columns |
| `sales_orders.status` | Column profile — deep-dive on one column |
| `sales_orders status` (space-separated) | Same as dot notation — column profile |

If no target is provided, ask:
> "Which table (or table.column) would you like to profile?"

---

## Step 2: Cost Check

Profile queries perform full table scans — they must go through `db-cost-gate`.

For **SQLite**: no cost check needed; proceed freely.

For **Oracle / Athena**: before running any `COUNT`, `COUNT DISTINCT`, `MIN`, or `MAX`
query, apply the `db-cost-check` skill to estimate scan cost. If the estimate exceeds
the configured threshold, pause and ask for confirmation ("cost confirmed") before
continuing.

Tell the user what is happening:
> "Profiling `{table_name}` — this will scan the full table. Estimating cost first…"

---

## Step 3a: Table Profile

Run the following queries, adapting syntax per dialect.

### Row count

```sql
-- All dialects
SELECT COUNT(*) AS total_rows FROM {table};

-- Oracle: qualify with schema
SELECT COUNT(*) AS total_rows FROM {SCHEMA}.{TABLE};

-- Athena
SELECT COUNT(*) AS total_rows FROM {database}.{table};
```

### Per-column stats

Run a single wide SELECT to minimize round trips. Build it dynamically from the column
list obtained via `db-introspect`.

**SQLite / Oracle / Athena template (adapt syntax):**

```sql
SELECT
  COUNT(*) AS total_rows,

  -- Repeat the following block for each column {col}:
  COUNT({col})                AS {col}__non_null,
  COUNT(DISTINCT {col})       AS {col}__distinct
  -- Add MIN / MAX for numeric and date columns:
  -- MIN({col})               AS {col}__min,
  -- MAX({col})               AS {col}__max
FROM {table};
```

**Athena optimization:** Use `approx_distinct({col})` instead of `COUNT(DISTINCT {col})`
for large tables. Label the output as "approx distinct" in the report.

**Oracle LOB columns:** Skip `COUNT(DISTINCT)`, `MIN`, and `MAX` for CLOB/BLOB columns —
they will error. Note "LOB — stats skipped" in output.

### Top values for low-cardinality columns

After getting distinct counts, for any column where `distinct_count ≤ 20`, fetch the
full value distribution:

```sql
-- SQLite / Oracle
SELECT {col} AS value, COUNT(*) AS occurrences
FROM {table}
GROUP BY {col}
ORDER BY COUNT(*) DESC;

-- Athena
SELECT {col} AS value, COUNT(*) AS occurrences
FROM {database}.{table}
GROUP BY {col}
ORDER BY COUNT(*) DESC;
```

---

### Table Profile Output Format

```
## Profile: {SCHEMA}.{TABLE_NAME}

**Total rows:** {N:,}
**Columns:** {N}
**Profiled on:** {today's date}

### Column Stats

| Column | Type | Non-null | Null % | Distinct | Min | Max | Notes |
|--------|------|----------|--------|----------|-----|-----|-------|
| order_id | INTEGER | 1,000 / 1,000 | 0% | 1,000 | 1 | 1,000 | PK — all unique |
| status | TEXT | 1,000 / 1,000 | 0% | 4 | — | — | Low cardinality ↓ |
| amount | REAL | 950 / 1,000 | 5% | 831 | 0.00 | 9,999.99 | |
| deleted_at | TEXT | 12 / 1,000 | 98.8% | 12 | 2023-01-04 | 2024-11-22 | Sparse — soft delete |

### Value Distributions (low-cardinality columns)

**status** (4 distinct values):
| Value | Count | % |
|-------|-------|---|
| shipped | 412 | 41.2% |
| pending | 310 | 31.0% |
| cancelled | 198 | 19.8% |
| refunded | 80 | 8.0% |

### Anomaly Flags

{Check for and flag each of the following if detected:}
```

---

### Anomaly Detection (Table Profile)

Flag any of the following patterns in an "Anomaly Flags" section:

| Condition | Flag |
|-----------|------|
| Column null % > 50% | ⚠️ `{col}` is {N}% null — may be optional, legacy, or a soft-delete indicator |
| Column null % = 100% | ⚠️ `{col}` is entirely null — possibly deprecated or not yet populated |
| `distinct_count = total_rows` and column is not the PK | ⚠️ `{col}` appears to be a natural key — every value is unique |
| `distinct_count = 1` | ⚠️ `{col}` is a constant — only one distinct value across all rows |
| Column named `deleted_at`, `is_deleted`, `is_active` | ⚠️ Soft-delete pattern detected — see gotchas |
| MIN = MAX for a numeric column with >1 row | ⚠️ `{col}` has no variance — possible constant or stub data |
| Total rows = 0 | ⚠️ Table is empty |

If no anomalies detected: "No anomalies detected."

---

## Step 3b: Column Profile (Deep-Dive)

When a specific column is requested, run a focused profile on that column only.

### Stats query

```sql
-- SQLite / Oracle
SELECT
  COUNT(*)                        AS total_rows,
  COUNT({col})                    AS non_null_count,
  COUNT(DISTINCT {col})           AS distinct_count,
  MIN({col})                      AS min_val,
  MAX({col})                      AS max_val,
  AVG(CAST({col} AS FLOAT))       AS avg_val   -- numeric only; skip for text/date
FROM {table};

-- Athena
SELECT
  COUNT(*)                        AS total_rows,
  COUNT({col})                    AS non_null_count,
  approx_distinct({col})          AS approx_distinct,
  MIN({col})                      AS min_val,
  MAX({col})                      AS max_val,
  AVG(CAST({col} AS DOUBLE))      AS avg_val   -- numeric only
FROM {database}.{table};
```

### Value distribution

Always fetch value distribution for column profiles (up to top 50):

```sql
-- SQLite / Oracle
SELECT {col} AS value, COUNT(*) AS occurrences
FROM {table}
GROUP BY {col}
ORDER BY COUNT(*) DESC
FETCH FIRST 50 ROWS ONLY;   -- Oracle: use FETCH FIRST; SQLite: use LIMIT 50

-- Athena
SELECT {col} AS value, COUNT(*) AS occurrences
FROM {database}.{table}
GROUP BY {col}
ORDER BY COUNT(*) DESC
LIMIT 50;
```

### Numeric percentiles (if column is numeric)

```sql
-- SQLite (approximation using ordered sampling)
SELECT {col} FROM {table}
WHERE {col} IS NOT NULL
ORDER BY {col}
LIMIT 1 OFFSET (SELECT COUNT({col}) FROM {table} WHERE {col} IS NOT NULL) * 25 / 100;
-- Run once each for p25, p50, p75 by changing the multiplier

-- Oracle 12c+
SELECT
  PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {col}) AS p25,
  PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY {col}) AS p50,
  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {col}) AS p75
FROM {SCHEMA}.{TABLE}
WHERE {col} IS NOT NULL;

-- Athena
SELECT
  approx_percentile({col}, 0.25) AS p25,
  approx_percentile({col}, 0.50) AS p50,
  approx_percentile({col}, 0.75) AS p75
FROM {database}.{table}
WHERE {col} IS NOT NULL;
```

Only run percentile queries for numeric columns (INTEGER, FLOAT, DECIMAL, NUMBER, DOUBLE).
Skip for TEXT, VARCHAR, DATE, TIMESTAMP, BOOLEAN.

---

### Column Profile Output Format

```
## Profile: {TABLE_NAME}.{COLUMN_NAME}

**Type:** {data type}
**Nullable:** {Yes / No}
**Role:** {PK | FK → ref_table.ref_col | standard column}

### Statistics

| Metric | Value |
|--------|-------|
| Total rows | {N:,} |
| Non-null | {N:,} ({pct}%) |
| Null | {N:,} ({pct}%) |
| Distinct values | {N:,} |
| Min | {val} |
| Max | {val} |
| Avg | {val} *(numeric only)* |
| p25 / p50 / p75 | {val} / {val} / {val} *(numeric only)* |

### Value Distribution (top {N} of {total_distinct} distinct values)

| Value | Count | % of total |
|-------|-------|------------|
| {val} | {N}   | {pct}%     |
| NULL  | {N}   | {pct}%     |   ← include nulls as a row if present

### Interpretation

{1–3 sentences interpreting what this column's profile reveals:
- For status/type columns: what the values suggest about business states
- For numeric columns: whether distribution looks healthy or suspicious (all zeros, outliers)
- For date columns: what the range implies about data freshness or history
- For ID columns: whether uniqueness holds as expected}

### Anomaly Flags
{List any flags from the anomaly table above, or "None detected."}
```

---

## Step 6: Offer to Save Anomaly Flags (Table Profile Only)

Profile statistics (row counts, null rates, distributions) are time-sensitive and do
**not** belong in the knowledge base — they go stale and mislead future readers.

**The exception is anomaly flags.** Flags like "soft-delete pattern detected" or
"`status` has only 4 distinct values" are structural facts about the table, not
snapshots. These map directly to the Gotchas section of a KB entry and are worth
persisting.

**Only offer this step when:**
- The target was a full **table profile** (not a column deep-dive)
- At least one anomaly flag was detected

If both conditions are met, ask after presenting results:

> "Found {N} anomaly flag(s) for `{table_name}`. Save them to the knowledge base
> gotchas? (y/n)"

Do not ask if no anomalies were detected — there is nothing worth saving.

---

### Case A: KB entry already exists

First, read `.claude/db-connections/active.yaml` to determine `name` (the active connection name)
and the active schema from `schema_scope`.

File `db-knowledge/{connection-name}/{schema}/{table_name}.md` exists.

1. Read the file
2. Locate the `## Gotchas` section
3. Append any flags not already documented there (match by flag text to avoid
   duplicates)
4. Write the updated file
5. Confirm: "Added {N} gotcha(s) to `db-knowledge/{connection-name}/{schema}/{table_name}.md`."

Format each appended flag as a bullet using the anomaly flag text from Step 3a:

```markdown
- ⚠️ **Soft delete** — `deleted_at` is {N}% null. Always filter `WHERE deleted_at IS NULL`.
- ⚠️ **Sparse column** — `{col}` is {N}% null — may be optional, legacy, or not yet populated.
- ⚠️ **Constant column** — `{col}` has only one distinct value across all rows.
```

---

### Case B: No KB entry exists

File `db-knowledge/{connection-name}/{schema}/{table_name}.md` does not exist.

Create a minimal stub containing only the gotchas and open questions sections, then
confirm and suggest the next step:

```markdown
# {TABLE_NAME}

**Schema:** `{schema}`
**Last Updated:** {today's date}
**Rows (approx):** {N from profile}

## Gotchas

- ⚠️ {flag 1}
- ⚠️ {flag 2}

## Open Questions

- [ ] Profile run {today's date} — full documentation pending.
```

After writing:
> "Created `db-knowledge/{connection-name}/{schema}/{table_name}.md` with {N} gotcha(s). This is a
> stub — run `/db-explain {table_name}` to add grain, business purpose, and key
> column descriptions."

---

### Column profile: no save offer

Do **not** offer to save anomaly flags for column deep-dives. Column-level findings
(null rate, value distribution, interpretation) should be added to a table KB entry
via `/db-explain column: {table}.{col}`, which handles that workflow correctly.

---

## Notes

- Always run `db-introspect` before profiling if you don't already have the column list for the target table — you need column names and types to build the stats query
- For very wide tables (>40 columns), profile only the first 20 columns by default and offer to profile the rest: "Table has {N} columns — profiling first 20. Type 'profile all' to continue."
- Do not profile system tables, temp tables, or metadata views — skip and note if accidentally targeted
- Snowflake and Salesforce are not yet fully supported — acknowledge and offer to proceed with manual guidance
