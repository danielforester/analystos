---
name: db-joins
description: Discover and document join paths between two or more tables. Uses FK metadata as the primary source, then falls back to column name matching for heuristic discovery. Checks cardinality and flags fan-out risk. Outputs in the structured relationship block format used in _schema-overview.md.
user-invocable: true
argument-hint: "<table1> [table2 ...]"
allowed-tools:
  - Read
  - Bash
  - Write
---

# /db-joins

**Invocation:** `/db-joins {table_a} {table_b} [table_c ...]`

Use this command when an analyst wants to know how to join two or more tables safely:
what the join keys are, what cardinality to expect, whether a pre-filter is required,
and what gotchas to watch for.

---

## Behavior Overview

1. Parse target tables
2. Run introspection to find FK relationships
3. Discover heuristic join paths for any pair with no FK
4. Check cardinality for each discovered path
5. Flag fan-out risks
6. Output structured relationship blocks
7. Offer to append to `_schema-overview.md`

---

## Step 1: Parse Target Tables

Accept 2 or more table names as arguments (space-separated or comma-separated).

If fewer than 2 tables are provided, ask:
> "Please name at least two tables. For example: `/db-joins sales_orders sales_customers`"

Normalize table names: strip schema prefix if provided (the analyst may type
`sales.orders` or just `orders`) — use the schema from the active connection config.

---

## Step 2: Introspect FK Relationships

Use `db-introspect` to load the full column and FK metadata for all target tables.
(If `db-introspect` has already been run in this session and the output is in context,
reuse it rather than re-querying.)

For each pair of target tables (A, B), check:

1. **Direct FK A → B:** Does table A have a column with a FK constraint pointing to table B?
2. **Direct FK B → A:** Does table B have a column pointing to table A?
3. **Indirect via bridge:** Is there a third table C in scope that has FKs to both A and B?
   (Only check for bridge tables if the analyst named 3+ tables, or if direct FKs are absent.)

For each FK found, record:
- FK column name (source)
- Referenced table and column (target)
- Whether the FK is nullable (affects LEFT vs INNER join recommendation)

---

## Step 3: Heuristic Join Discovery

If no FK relationship is found between a pair, scan for column name matches:

**Name-match heuristics (in priority order):**

| Pattern | Interpretation |
|---------|---------------|
| `table_b.id` = `table_a.table_b_id` | Standard FK pattern — high confidence |
| `table_b.id` = `table_a.{table_b_singular}_id` | Singular form match — high confidence |
| Both tables share a column with the same name and type | Possible natural key join — medium confidence |
| Column in A named `{something}_id` matches a PK in B | Possible FK, no constraint — medium confidence |

For each heuristic match, label it clearly: **"No FK constraint — join inferred from column names. Verify before relying on this join."**

If no join path is found at all between two tables, report:
> "No direct join path found between `{table_a}` and `{table_b}`. These tables may not be
> directly related, or may share only a semantic relationship not expressed in column names.
> Consider asking the data owner."

---

## Step 4: Cardinality Check

For each discovered join path (FK or heuristic), run cardinality queries to determine the
relationship type and detect fan-out risk. Route through `db-cost-gate` first on Oracle/Athena.

```sql
-- Count rows on each side
SELECT COUNT(*) AS total_rows FROM {table_a};
SELECT COUNT(*) AS total_rows FROM {table_b};

-- Count distinct values of the join key on each side
-- SQLite / Oracle
SELECT COUNT(DISTINCT {join_key}) AS distinct_keys FROM {table_a};
SELECT COUNT(DISTINCT {join_key}) AS distinct_keys FROM {table_b};

-- Athena (use approx for large tables)
SELECT approx_distinct({join_key}) AS approx_distinct_keys FROM {database}.{table_a};
SELECT approx_distinct({join_key}) AS approx_distinct_keys FROM {database}.{table_b};

-- Fan-out check: find keys on the "many" side with multiple rows
-- Run on whichever table is suspected to be the "many" side
SELECT {join_key}, COUNT(*) AS row_count
FROM {table}
GROUP BY {join_key}
HAVING COUNT(*) > 1
LIMIT 5;
```

**Cardinality interpretation:**

| Condition | Cardinality label |
|-----------|-------------------|
| `distinct_keys_A = total_rows_A` and `distinct_keys_B = total_rows_B` | 1:1 |
| `distinct_keys_A = total_rows_A` and `distinct_keys_B < total_rows_B` | 1:many (B is many) |
| `distinct_keys_A < total_rows_A` and `distinct_keys_B = total_rows_B` | many:1 (A is many) |
| `distinct_keys_A < total_rows_A` and `distinct_keys_B < total_rows_B` | many:many — bridge table likely needed |

**Fan-out threshold:** If the HAVING query returns any rows (keys with >1 row on the many side),
flag fan-out risk. Show the top 5 examples.

---

## Step 5: Join Type Recommendation

Determine the recommended join type:

| Condition | Recommendation |
|-----------|---------------|
| FK column is NOT NULL | `INNER JOIN` — every left row has a right-side match |
| FK column is nullable | `LEFT JOIN` — some left rows may have no right-side match |
| Many:many cardinality detected | `INNER JOIN` via bridge table — never join directly |
| Heuristic match (no FK constraint) | `LEFT JOIN` by default — verify nullability first |

---

## Step 6: Output — Structured Relationship Blocks

Produce one relationship block per join path, following the format from
`_schema-overview-template.md` Section 3.

```
## Join Paths: {TABLE_A} ↔ {TABLE_B}

---

### {LEFT_TABLE} → {RIGHT_TABLE}

\`\`\`
Cardinality : {1:1 | 1:many | many:1 | many:many}
Join type   : {INNER | LEFT | Depends — see notes}
Left key    : {left_table}.{left_key_column}
Right key   : {right_table}.{right_key_column}
Direction   : {Preferred: left → right | Bidirectional}
Pre-filters : {Required filters before joining, or "None required"}
Gotchas     : {Fan-out risk, null key risk, soft-delete filter, or "None known"}
Source      : {FK constraint | Heuristic — verify before use}
\`\`\`

Cardinality check results:
- `{left_table}`: {total_rows:,} rows, {distinct_keys:,} distinct `{key}` values
- `{right_table}`: {total_rows:,} rows, {distinct_keys:,} distinct `{key}` values
{Fan-out examples if detected:}
- ⚠️ Fan-out detected: `{key}` value `{example_val}` appears {N} times in `{many_table}`

\`\`\`sql
-- Safe join example
SELECT
    a.{left_key},
    a.{another_col},
    b.{right_col}
FROM {left_table} a
{INNER|LEFT} JOIN {right_table} b
    ON a.{left_key} = b.{right_key}
{WHERE pre_filter if required}
\`\`\`

```

### Fan-out warning block (if detected)

If fan-out is detected, add a prominent warning before the example query:

```
⚠️ FAN-OUT RISK
━━━━━━━━━━━━━━━
Joining `{left_table}` to `{right_table}` on `{key}` will produce multiple rows
per `{left_table}` row because some `{key}` values appear more than once in
`{right_table}`.

Examples of duplicated keys:
| {key} | Occurrences in {right_table} |
|-------|------------------------------|
| {val} | {N} |
| {val} | {N} |

To avoid inflated aggregations:
1. Add a filter before joining: WHERE {right_table}.{filter_col} = {expected_value}
2. Or use a subquery/CTE to deduplicate {right_table} first
3. Or verify with: SELECT {key}, COUNT(*) FROM {right_table} GROUP BY 1 HAVING COUNT(*) > 1
```

---

## Step 7: Summary Table

After all relationship blocks, output a compact summary:

```
## Relationship Map

| From | To | Via | Cardinality | Join Type | Pre-filter? | Risk |
|------|----|-----|-------------|-----------|-------------|------|
| `table_a` | `table_b` | `table_a.b_id = table_b.id` | 1:many | LEFT | No | Low |
| `table_b` | `table_c` | `table_b.c_id = table_c.id` | many:1 | INNER | ✅ status filter | ⚠️ Fan-out |
```

---

## Step 8: Offer to Save

After presenting join paths, ask:

> "Add these relationship blocks to `.claude/db-knowledge/{schema}/_schema-overview.md`?
> (y = append to Section 3 and update Section 4 summary table, n = skip)"

If yes and `_schema-overview.md` exists:
- Find Section 3 (Key Relationships) and append the new relationship blocks
- Find Section 4 (Relationship Map summary table) and add/update the relevant rows
- Confirm: "Appended join paths to `_schema-overview.md`."

If the file does not exist:
- Offer to run `/db-orient` first to create a full overview, or save just the join paths as a standalone file:
  `.claude/db-knowledge/{schema}/_joins-{table_a}-{table_b}.md`

---

## Multi-Table Paths

When 3+ tables are provided, analyze all pairwise combinations and look for a logical join chain:

**Example: `/db-joins sales_orders sales_customers hr_employees`**

1. Check `sales_orders ↔ sales_customers`
2. Check `sales_orders ↔ hr_employees`
3. Check `sales_customers ↔ hr_employees`
4. If a chain exists (e.g., orders → customers, orders → employees), present it as a joined query example:

```sql
-- Three-way join example
SELECT
    o.order_id,
    c.customer_name,
    e.employee_name AS rep_name
FROM sales_orders o
LEFT JOIN sales_customers c ON o.customer_id = c.customer_id
LEFT JOIN hr_employees e    ON o.employee_id = e.employee_id
WHERE c.deleted_at IS NULL  -- exclude churned customers
  AND e.is_active = 1       -- exclude terminated reps
```

---

## Notes

- Always prefer FK-backed join paths over heuristic ones; label the source clearly in output
- Cardinality checks scan the full table on Oracle/Athena — always run cost check first
- For SQLite, `COUNT(DISTINCT)` is supported natively; no approximation needed
- Do not recommend `CROSS JOIN` or Cartesian products under any circumstances
- If the analyst names tables from different schemas, note that cross-schema joins may require explicit schema qualification and may not be supported in all environments (e.g., Athena cross-database queries have restrictions)
- Snowflake and Salesforce are not yet fully supported — acknowledge and offer manual guidance
