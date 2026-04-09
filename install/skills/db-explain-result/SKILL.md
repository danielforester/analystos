---
name: db-explain-result
description: This skill should be used immediately after executing a SQL query — either automatically via /db-query or when the analyst requests interpretation of a result set. Apply it to synthesize a plain-English explanation of what the numbers show, infer result grain, and flag anomalies such as fan-out inflation, unexpected nulls, suspiciously round aggregates, and empty result sets.
---

# DB Explain Result Skill

Use this skill after executing a SQL query to help the analyst understand what the result means,
whether it looks correct, and what to investigate if something seems off.

---

## Inputs

When this skill is invoked, you will have:
- **query** — the SQL that was executed
- **result** — the rows and columns returned (a sample if the result is large)
- **row_count** — total number of rows returned
- **execution_time_ms** — how long the query took (optional; use if available)
- **connection_type** — the active database type (sqlite, oracle, athena, snowflake, salesforce)

If the result set is large (> 50 rows), work from the first 25 rows plus the row count.
Do not ask for additional data unless a specific anomaly requires it.

---

## Behavior Overview

1. Infer the result grain
2. Summarize what the result shows in plain English
3. Check for anomalies
4. Suggest next steps if anomalies are found or the result raises obvious questions

---

## Step 1: Infer Result Grain

Determine what one row in the result represents by examining:
- The `GROUP BY` clause (if present) — the grain is "one row per {group-by columns}"
- `DISTINCT` — signals deduplication; grain is the distinct key
- No aggregation + no DISTINCT — grain is likely the underlying table's grain (check for fan-out)
- Aggregate without GROUP BY — one summary row; grain is "aggregate across all rows"

State the grain explicitly:
> "One row per {grain}."

If grain is unclear, say: "Grain unclear from query structure — verify that the row count ({N}) matches your expectations for the entity being measured."

---

## Step 2: Summarize the Result

Write 2–4 sentences in plain English:
- What question does this result answer?
- What does the data show at a high level? (ranges, totals, distribution shape)
- Any immediately notable observation (e.g., "Most orders are in 'completed' status; 'cancelled' represents ~8%")

Keep this grounded in the actual values — do not editorialize or interpret business implications
beyond what the data shows.

---

## Step 3: Flag Anomalies

Check each signal below. Report any that apply. If none apply, state: "No anomalies detected."

### A. Empty Result Set (0 rows)
If `row_count = 0`:
> ⚠️ **Empty result** — The query ran successfully but returned no rows.
> Possible causes:
> - A WHERE filter is too narrow (check date ranges, status values, join conditions)
> - A soft-delete filter may be excluding all rows — check if the table uses `deleted_at`, `is_deleted`, or `is_active` and whether the filter is appropriate
> - An INNER JOIN is dropping all rows — try converting to LEFT JOIN to identify which join is eliminating rows
> - The target table is empty or not yet populated

### B. Fan-Out (Row Count Higher Than Expected)
Signals:
- Query joins two or more tables without a GROUP BY or DISTINCT
- Row count is a round multiple of an expected count (e.g., exactly 3× the number of orders)
- The query's FROM / JOIN structure includes a one-to-many relationship

If detected:
> ⚠️ **Possible fan-out** — The result has {N} rows, but the query joins `{table_a}` to `{table_b}` without deduplication. If `{table_b}` has multiple rows per `{table_a}` key, aggregate totals will be inflated.
> Check: `SELECT COUNT(*), COUNT(DISTINCT {join_key}) FROM {table_b}` to confirm cardinality.

### C. Suspiciously Round Aggregates
Signals:
- A SUM or COUNT returns exactly 0
- A percentage is exactly 0%, 100%, or a perfect round number (50.000, 25.000)
- All rows return the same value for an aggregate column

If detected:
> ⚠️ **Suspiciously uniform aggregate** — `{column}` returns {value} for all rows (or exactly 0 / 100%). This may indicate:
> - A filter that is too broad or too narrow
> - A join condition that is always true or always false
> - A data quality issue (all values are the same in the source)
> Verify by running a quick distribution check on the column.

### D. Unexpected Nulls
Signals:
- A column that should never be null (a PK, a required status field, an amount) contains NULL in results
- More than 20% of a non-nullable column's values are NULL

If detected:
> ⚠️ **Unexpected NULLs in `{column}`** — {N} rows have NULL where a value is expected.
> Possible causes:
> - A LEFT JOIN is not finding matches in the right-hand table
> - The source data has quality issues
> - A CASE expression has an unhandled branch
> Check: filter for `WHERE {column} IS NULL` to isolate affected rows.

### E. Duplicate Apparent Primary Keys
Signals:
- The result contains a column that appears to be a PK (named `id`, `{table}_id`, `order_id`, etc.)
- That column's values repeat across rows
- No GROUP BY or aggregation is present

If detected:
> ⚠️ **Duplicate key values in `{id_column}`** — The same `{id_column}` value appears in multiple rows without aggregation. This is consistent with fan-out from a one-to-many join. Confirm cardinality before using this result for counting or summing.

### F. Date / Time Range Anomalies
Signals:
- The result covers a narrower date range than the query implies (e.g., query says "all time" but all dates are from one month)
- Future dates appear in a created/updated column
- The earliest date is much earlier than expected (epoch dates, 1900-01-01, etc.)

If detected:
> ⚠️ **Date range anomaly** — Dates in `{col}` range from {min} to {max}. {Describe what seems off.}
> This may indicate a data quality issue or a filter that is not behaving as expected.

---

## Step 4: Suggest Next Steps

After summarizing and flagging anomalies, offer 1–3 concrete follow-up actions if relevant:

- If empty result: suggest the specific filter to relax or join to convert
- If fan-out suspected: provide the cardinality check query
- If nulls found: provide the isolation query (`WHERE {col} IS NULL`)
- If result looks correct: offer to save the query via `/db-capture` or explain a specific column

Example:
> **Next steps:**
> 1. Verify cardinality: `SELECT COUNT(*), COUNT(DISTINCT order_id) FROM order_items WHERE order_id IS NOT NULL;`
> 2. If cardinality confirms fan-out, add `GROUP BY order_id` and use `SUM(line_amount)` instead of summing the join result.

If the result looks correct and no anomalies are detected:
> "Result looks clean. {1 sentence on what it confirms.} Let me know if you'd like to drill into any segment or save this query."

---

## Output Format

Present the interpretation in this order:

```
**Grain:** One row per {grain}.

**What this shows:**
{2–4 sentence summary}

**Anomalies:**
{List detected anomalies, or "None detected."}

**Next steps:**
{1–3 suggestions, or omit if result is clean and no follow-up is needed}
```

Keep the total output concise — an analyst should be able to read it in under 30 seconds.
Do not repeat the query back to the analyst; they can see it above the result.

---

## Notes

- This skill interprets the result in front of you — do not fabricate values or extrapolate beyond what the rows show
- If the result is truncated (> 50 rows shown), note this: "Showing first {N} of {total} rows — anomaly checks based on visible sample"
- For Salesforce results: note that SOQL aggregate queries have governor limits; a COUNT() result near 50,000 may indicate the query hit the synchronous query row limit
- For Athena results: if execution time is available and > 30 seconds, note the cost implication and suggest adding partition filters
