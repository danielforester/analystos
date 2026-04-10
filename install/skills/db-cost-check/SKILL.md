---
name: db-cost-check
description: This skill should be used before executing any SELECT query on Oracle, Athena, Snowflake, or Salesforce — especially when the db-cost-gate hook fires or when queries touch large tables, wide date ranges, or lack partition filters. Provides dialect-specific cost estimation and a go/no-go decision before execution.
user-invocable: false
---

# DB Cost Check Skill

Use this skill **before executing any SELECT query** on Oracle, Athena, Snowflake, or Salesforce.
It provides dialect-specific cost estimation so analysts can make informed decisions before
accidentally triggering expensive scans or hitting governor limits.

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

## Snowflake — Cost Estimation

Snowflake pricing is compute-based (credits per second of warehouse time) rather than
purely data-volume-based, but bytes scanned is still the best proxy for cost estimation
before execution.

### Step 1: Check row count from INFORMATION_SCHEMA

```sql
SELECT row_count, bytes
FROM {database}.information_schema.tables
WHERE table_schema = '{SCHEMA}' AND table_name = '{TABLE}';
```

Use this for the primary table(s) in the query's FROM clause. Compare `row_count`
against `warn_rows` from `active.yaml`.

### Step 2: Run EXPLAIN (optional, for complex queries)

```sql
EXPLAIN {your_query};
```

Parse the JSON output for `"statistics": {"partitionsTotal": N, "bytesAssigned": N}`.
Note: Snowflake EXPLAIN output is JSON; extract `bytesAssigned` if present.

### Step 3: Report to the analyst

```
📊 Snowflake Cost Estimate
━━━━━━━━━━━━━━━━━━━━━━━━━
Estimated rows (source): {row_count from INFORMATION_SCHEMA, formatted with commas}
Estimated bytes         : {bytes, formatted as GB if > 1 GB}
Warehouse               : {warehouse from active.yaml if available}
Threshold               : {warn_rows from active connection}
Result                  : {WITHIN THRESHOLD ✅ | EXCEEDS THRESHOLD ⚠️}
```

If the estimate **exceeds** `warn_rows`:
> "⚠️ The source table has **{N}** rows, which exceeds your configured threshold of {warn_rows}.
> Snowflake cost depends on warehouse size and query complexity. Type **\"cost confirmed\"** to
> proceed, or:
> - Add WHERE filters to reduce rows scanned
> - Use `SAMPLE (1)` for exploratory queries (returns ~1% of rows)
> - Use `APPROX_COUNT_DISTINCT()` instead of exact `COUNT(DISTINCT)` for large aggregations"

### Notes
- Snowflake caches query results — identical queries within 24 hours are free (no recompute)
- Clustering keys on large tables can dramatically reduce bytes scanned; check `SHOW TABLES` for `clustering_key`
- Virtual warehouse size affects speed and credit burn — `XSMALL` is fine for most analytical queries

---

## Salesforce — Cost Estimation (Governor Limit Check)

Salesforce does not have a query cost in dollars. The risk is hitting **governor limits**
that cause hard errors. The cost check for Salesforce is a pre-flight record count.

### Step 1: Count records with the same filters

```soql
SELECT COUNT()
FROM {Object}
WHERE IsDeleted = false AND {same_filters_as_full_query}
```

This is a lightweight aggregate query — it does not return row data and is fast even on large objects.

### Step 2: Interpret

Compare the COUNT() result against `warn_records` from `active.yaml` (default: 50,000).

| Count | Verdict |
|-------|---------|
| < warn_records | Safe to run synchronously |
| ≥ warn_records but < 50,000 | Warn — close to limit |
| ≥ 50,000 | Block — exceeds synchronous SOQL row limit |

### Step 3: Report to the analyst

```
📊 Salesforce Governor Limit Check
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Estimated record count: {N}
Synchronous row limit : 50,000
Configured threshold  : {warn_records from active connection}
Result                : {WITHIN THRESHOLD ✅ | APPROACHING LIMIT ⚠️ | EXCEEDS LIMIT 🚫}
```

If count **≥ 50,000**:
> "🚫 This query would return approximately **{N}** records, which exceeds Salesforce's
> synchronous SOQL row limit of 50,000. The query will fail with a `QUERY_ROW_LIMIT_EXCEEDED`
> error. To proceed:
> - Add more selective WHERE filters (indexed fields: `Id`, `CreatedDate`, `OwnerId`, `RecordTypeId`)
> - Use date range pagination: query one time period at a time
> - For full exports, use the Bulk API or Data Loader (outside Claude Code scope)"

If count is **between warn_records and 50,000**:
> "⚠️ This query will return approximately **{N}** records, approaching the 50,000 synchronous
> limit. Type **\"cost confirmed\"** to proceed."

If **within** `warn_records`:
> "✅ Record count looks fine ({N} records, within your {warn_records} threshold). Proceeding."

### Notes
- Non-selective WHERE clauses (no indexed field) on large objects may time out even before hitting the row limit
- `SELECT COUNT()` itself is not subject to the row limit — it always returns a single integer
- For Salesforce, always verify picklist values in WHERE clauses are valid before running the count

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
