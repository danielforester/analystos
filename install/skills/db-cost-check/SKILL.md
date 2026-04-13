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

### Step 1: Run EXPLAIN PLAN via the wrapper script

```bash
python scripts/oracle_connect.py --query "{paste the query here}" --explain
```

The script runs `EXPLAIN PLAN FOR` (does not execute the query), reads the plan via
`DBMS_XPLAN.DISPLAY()`, and prints structured output:

```
EXPLAIN PLAN output:
{plan text}
---
estimated_rows: 5,000,000
full_table_scans: ORDERS, CUSTOMERS
```

Read `estimated_rows:` for the root cardinality and `full_table_scans:` for high-risk
table access patterns. If the script prints `estimated_rows: unknown`, the plan table
could not be read — ask the analyst if they want to proceed without an estimate.

### Step 2: (No longer needed — handled by wrapper)

The wrapper reads `plan_table` automatically. For deeper plan inspection, use:

```bash
python scripts/oracle_connect.py --query "SELECT operation, options, object_name, cardinality, cost FROM plan_table WHERE plan_id = (SELECT MAX(plan_id) FROM plan_table) ORDER BY id" --format csv
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

### Step 1: Run EXPLAIN via the wrapper script

```bash
python scripts/athena_connect.py --query "{paste the query here}" --explain
```

The script runs `EXPLAIN`, polls for completion, and prints structured output:

```
EXPLAIN output:
{raw Presto/Trino plan text}
---
estimated_bytes: 2684354560
estimated_gb: 2.50
```

Read the `estimated_bytes:` and `estimated_gb:` lines from stdout.
If the script prints `estimated_bytes: unknown`, the plan text could not be parsed —
flag this to the analyst and ask if they want to proceed without an estimate.

### Step 2: Parse the output

From the raw plan text, also note:
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

### Step 1: Run EXPLAIN USING TABULAR via the wrapper script

```bash
python scripts/snowflake_connect.py --query "{paste the query here}" --explain
```

The script runs `EXPLAIN USING TABULAR` (does not execute the query), aggregates
`bytesAssigned` across all plan rows, and prints structured output:

```
EXPLAIN output:
{tabular plan as CSV}
---
estimated_bytes: 536870912
estimated_gb: 0.50
partitions_total: 50
```

Read `estimated_bytes:` and `estimated_gb:` for cost comparison against `warn_bytes`.
Read `partitions_total:` to understand how many micro-partitions will be scanned.
If the script prints `estimated_bytes: unknown`, the plan could not be parsed — fall back to Step 2.

### Step 2: Fallback — Check row count from INFORMATION_SCHEMA

Use this when EXPLAIN is unavailable or the query is a simple single-table scan:

```sql
SELECT row_count, bytes
FROM {database}.information_schema.tables
WHERE table_schema = '{SCHEMA}' AND table_name = '{TABLE}';
```

Compare `row_count` against `warn_rows` from `active.yaml` as a proxy for scan cost.

### Step 3: Report to the analyst

```
📊 Snowflake Cost Estimate
━━━━━━━━━━━━━━━━━━━━━━━━━
Estimated bytes scanned : {estimated_gb} GB  (from EXPLAIN USING TABULAR)
Partitions to scan      : {partitions_total}
Warehouse               : {warehouse from active.yaml}
Threshold (bytes)       : {warn_bytes from active connection, formatted as GB}
Threshold (rows)        : {warn_rows from active connection, formatted with commas}
Result                  : {WITHIN THRESHOLD ✅ | EXCEEDS THRESHOLD ⚠️}
```

If the estimate **exceeds** `warn_bytes`:
> "⚠️ This query is estimated to scan **{X} GB**, which exceeds your configured threshold of
> {threshold_gb} GB. Snowflake cost depends on warehouse size and query complexity.
> Type **\"cost confirmed\"** to proceed, or:
> - Add WHERE filters to reduce partitions scanned
> - Use `SAMPLE (1)` for exploratory queries (returns ~1% of rows)
> - Use `APPROX_COUNT_DISTINCT()` instead of exact `COUNT(DISTINCT)` for large aggregations
> - Select only needed columns instead of `SELECT *`"

If only `warn_rows` is configured and source table **exceeds** `warn_rows`:
> "⚠️ The source table has **{N}** rows, which exceeds your configured threshold of {warn_rows}.
> Type **\"cost confirmed\"** to proceed."

### Notes
- Snowflake caches query results — identical queries within 24 hours are free (no recompute)
- Clustering keys on large tables can dramatically reduce partitions scanned; check `SHOW TABLES` for `clustering_key`
- Virtual warehouse size affects speed and credit burn — `XSMALL` is fine for most analytical queries
- `EXPLAIN USING TABULAR` never charges credits — always prefer it over guessing from row counts

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
