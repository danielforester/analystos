---
name: db-cost-check
description: Pre-execution cost estimation for database queries. Use this skill before running any query on Oracle or Athena to estimate rows scanned or bytes scanned, and compare against configured thresholds.
---

# DB Cost Check Skill

Use this skill **before executing any SELECT query** on Oracle or Athena. It provides
dialect-specific cost estimation so analysts can make informed decisions before
accidentally triggering expensive scans.

---

## When to Use This Skill

- Any time `db-cost-gate` blocks a query and asks for a cost check
- Proactively, before running queries that touch large tables (>1M rows, date-range scans, no partition filter)
- When the analyst asks "how expensive is this query?"

**Skip for:** SQLite (local, no cost), queries on small/temp tables, EXPLAIN queries themselves.

---

## Oracle — Cost Estimation

### Step 1: Run EXPLAIN PLAN

```sql
EXPLAIN PLAN FOR
{paste the query here};
```

This does **not** execute the query. It populates `PLAN_TABLE`.

### Step 2: Read the plan

```sql
SELECT
    id,
    parent_id,
    operation,
    options,
    object_owner || '.' || object_name AS object,
    cardinality          AS est_rows,
    bytes                AS est_bytes,
    cost                 AS relative_cost,
    partition_start,
    partition_stop
FROM plan_table
WHERE plan_id = (SELECT MAX(plan_id) FROM plan_table)
ORDER BY id;
```

### Step 3: Interpret

- **Root row (id = 0):** The `cardinality` column is the **total estimated output rows**.
- **Full table scans** (`TABLE ACCESS FULL`): Flag these — they scan every row regardless of WHERE clause.
- **Index range scans** (`INDEX RANGE SCAN`): Much cheaper; cardinality should be low.
- **Nested loops on large tables:** Multiply inner cardinality by outer — can explode.

### Step 4: Report to the analyst

Format your response as:

```
📊 Oracle Cost Estimate
━━━━━━━━━━━━━━━━━━━━━━━
Estimated output rows : {cardinality from root row, formatted with commas}
Relative cost (Oracle): {cost from root row}
Full table scans      : {list any TABLE ACCESS FULL operations}
Threshold             : {warn_rows from active connection}
Result                : {WITHIN THRESHOLD ✅ | EXCEEDS THRESHOLD ⚠️}
```

If the estimate **exceeds** `warn_rows`:
> "⚠️ This query is estimated to process **{N}** rows, which exceeds your configured
> threshold of {warn_rows}. Type **\"cost confirmed\"** to proceed, or revise the query
> to add more selective filters."

If within threshold:
> "✅ Cost estimate looks fine ({N} rows, within your {warn_rows} threshold). Proceeding."

---

## AWS Athena — Cost Estimation

### Step 1: Run EXPLAIN

```sql
EXPLAIN
{paste the query here};
```

Athena's EXPLAIN output is text-based. Look for:
- `Output rows` — estimated rows returned
- Fragment data-read estimates — Athena reports estimated data read per fragment

### Step 2: Parse the output

Key patterns to look for in EXPLAIN output text:
- `rows = X` — row count estimates at each node
- `Output[...] => [...]` — final output description
- Partition pruning notes — if the query hits ALL partitions, flag it

### Step 3: Calculate cost

Athena pricing: **~$5.00 per TB scanned** (verify current pricing at aws.amazon.com).

```
Estimated GB = {bytes from EXPLAIN} / 1,073,741,824
Estimated cost = (Estimated GB / 1024) * $5.00
```

### Step 4: Report to the analyst

Format your response as:

```
📊 Athena Cost Estimate
━━━━━━━━━━━━━━━━━━━━━━━
Estimated data scanned: {X} GB
Estimated cost        : ~${Y} (at $5/TB)
Partition pruning     : {Yes — N partitions hit | No — full scan}
Threshold             : {warn_bytes from active connection, formatted as GB}
Result                : {WITHIN THRESHOLD ✅ | EXCEEDS THRESHOLD ⚠️}
```

If the estimate **exceeds** `warn_bytes`:
> "⚠️ This query is estimated to scan **{X} GB** (~${Y}), which exceeds your configured
> threshold of {threshold_gb} GB. Type **\"cost confirmed\"** to proceed, or:
> - Add a partition filter on `{partition_column}` to reduce scan
> - Select only needed columns instead of `SELECT *`
> - Use `approx_distinct()` instead of `COUNT(DISTINCT)` if exact count isn't needed"

If within threshold:
> "✅ Cost estimate looks fine (~{X} GB / ~${Y}, within your {threshold_gb} GB threshold). Proceeding."

---

## SQLite — No Cost Check Needed

SQLite is a local file database. There is no network cost, cloud billing, or performance
concern that requires pre-flight estimation. Simply proceed with the query.

> "SQLite connection detected — no cost check required. Running query."

---

## Output Format Summary

Always return a structured cost estimate block followed by a clear go/no-go statement.
Never leave the analyst guessing whether they should proceed.

| Result | Action |
|---|---|
| Within threshold | State clearly, proceed with the query |
| Exceeds threshold | State clearly, show optimization tips, wait for "cost confirmed" |
| EXPLAIN not supported / error | Note the failure, ask analyst if they want to proceed without estimate |
