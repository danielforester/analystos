---
name: db-query
description: Natural-language to SQL. Interprets analyst intent, introspects the schema to confirm tables and columns exist, drafts dialect-correct SQL with inline comments, runs safety and cost checks, presents the query for review, executes on confirmation, and calls db-explain-result on the output. Highest-value daily-use command.
---

# /db-query

**Invocation:** `/db-query {natural language question or description}`

Use this command when an analyst knows what they want to find out and wants Claude to
translate that into a safe, validated, executable SQL query. Claude handles introspection,
safety, cost estimation, execution, and result interpretation — the analyst just describes
what they need.

---

## Behavior Overview

1. Parse the analyst's intent
2. Check the knowledge base for relevant gotchas and schema context
3. Introspect the relevant tables to confirm names, columns, types
4. Draft the SQL with inline explanatory comments
5. Run the safety check (read-only enforcement)
6. Run the cost check for the active connection type
7. Present the query with explanation and ask for confirmation
8. Execute on confirmation
9. Interpret results with `db-explain-result`
10. Offer to save the query if results are significant

---

## Step 1: Parse Intent

Read the analyst's natural-language request and identify:

- **The question being answered** — what does a correct result look like?
- **The likely tables involved** — named explicitly, or inferable from the question
- **The filters or scope** — time range, status, entity type, etc.
- **The output grain** — aggregate summary, per-record list, or distribution?
- **The dialect** — from active connection `type:` in `active.yaml`

**If the request is ambiguous**, ask one clarifying question before proceeding:
> "To clarify: are you looking for {interpretation A} or {interpretation B}?"

Do not draft a query when intent is unclear — guessing the wrong grain produces misleading results.

**Do not ask multiple questions.** If there are several ambiguities, pick the most likely
interpretation for minor ones and note your assumptions; ask only about the one ambiguity
that materially changes the query structure.

---

## Step 2: Check the Knowledge Base

Before writing any SQL, check `.claude/db-knowledge/` for context:

1. Read `_gotchas.md` — load any cross-schema warnings
2. If the question involves a specific schema, read `{schema}/_schema-overview.md` if it exists
3. For each table likely involved, check `{schema}/{table}.md` if it exists

If gotchas are found that are relevant to the query (e.g., a soft-delete pattern on a table
being queried), note them in the drafted query as comments:

```sql
-- ⚠️ Gotcha: orders uses soft deletes — filtered via deleted_at IS NULL
SELECT ...
FROM orders
WHERE deleted_at IS NULL   -- Required: filters out deleted records
```

---

## Step 3: Introspect Relevant Tables

Use the `db-introspect` skill to confirm:
- The table(s) exist in the active schema
- The column names used in the query are correct (exact case may matter for some dialects)
- The join keys exist and have compatible types
- Row counts for the primary table(s) (needed for cost estimation)

If a table or column **does not exist**:
> "I couldn't find `{table_name}` in the active schema. Tables available in `{schema}`:
> {list top 10 most likely matches by name similarity}. Did you mean one of these?"

Do not draft a query using tables or columns that aren't confirmed by introspection.

---

## Step 4: Draft the SQL

Write the SQL using the correct dialect for the active connection. Apply the `db-dialect`
skill for syntax. For Salesforce connections, apply the `db-soql` skill instead.

**Drafting rules:**
- Include inline comments explaining non-obvious choices (join type, filter rationale, alias reason)
- Apply known gotchas as WHERE filters without waiting for the analyst to ask
- Use `LIMIT` / `FETCH FIRST` / `TOP` appropriate for the dialect for any exploratory or sample queries — do not scan unbounded result sets unless the analyst's question explicitly requires it
- Prefer CTEs (`WITH` clauses) over nested subqueries for readability when the logic has 2+ steps
- Qualify table names with schema/database prefix per dialect norms
- Do not use `SELECT *` for Snowflake or Athena (columnar cost); list specific columns

**Typical query structure:**

```sql
-- Purpose: {one-line description of what this answers}
-- Returns: {grain — one row per what?}
-- Dialect: {sqlite | oracle | athena | snowflake | soql}

WITH {cte_name} AS (
    -- {explain why this CTE exists}
    SELECT ...
    FROM {schema}.{table}
    WHERE ...
)
SELECT
    {col1},
    {col2},
    COUNT(*) AS {count_alias}
FROM {cte_name}
    LEFT JOIN {other_table} ON ...   -- {explain join purpose and expected cardinality}
WHERE
    {filter}                         -- {why this filter}
GROUP BY {col1}, {col2}
ORDER BY {count_alias} DESC
LIMIT 100;
```

---

## Step 5: Safety Check

Before presenting the query, verify it passes the read-only constraint:
- The drafted query must be a `SELECT` (or `WITH ... SELECT`) statement
- No DDL or DML — if the analyst's question implies a write (e.g., "update the status"), clarify:
  > "This would require a write operation, which is not supported in read-only mode. I can show you the records that would be affected instead — would that help?"

The `db-safety` hook will also enforce this at execution time. The pre-flight check here
catches write intent early and redirects to a useful alternative.

---

## Step 6: Cost Check

Apply `db-cost-check` for the active connection type:

| Connection type | Cost check method |
|-----------------|------------------|
| `sqlite` | Skip — no cost |
| `oracle` | Run `EXPLAIN PLAN` on the drafted query |
| `athena` | Run `EXPLAIN` on the drafted query |
| `snowflake` | Check `information_schema.tables.row_count` for primary tables |
| `salesforce` | Run `SELECT COUNT()` with same WHERE filters |

If the cost check triggers (estimate exceeds threshold): present the estimate and wait for
`"cost confirmed"` before proceeding to Step 7.

---

## Step 7: Present Query and Ask for Confirmation

Show the analyst the query and a brief explanation:

```
Here's the query:

```sql
{the drafted SQL}
```

**What it does:** {1–2 sentence plain English explanation}
**Returns:** One row per {grain}
**Notes:** {any gotchas applied, any assumptions made}
{if cost check ran: **Estimated cost:** {cost estimate}}

Run this? (yes / no / edit: {describe change})
```

Wait for the analyst to respond before executing. Do not execute proactively.

If the analyst says **"edit: {change}"**, revise the query and re-present without re-running
the full introspection or cost check unless the table scope changed.

---

## Step 8: Execute

When the analyst confirms, execute the query against the active connection.

Tell the analyst what you're doing:
> "Running query against {display_name}…"

If execution fails with an error:
- Show the error message
- Diagnose the likely cause (syntax error, table not found, permission denied, governor limit hit)
- Offer a corrected query if the fix is clear; ask for clarification if it isn't

---

## Step 9: Interpret Results

After successful execution, immediately apply the `db-explain-result` skill:
- Pass the executed query, result set, row count, and execution time
- Present the interpretation below the result

Do not just show raw rows — always follow with the interpretation.

---

## Step 10: Offer to Save

After interpretation, if the result is significant (row count > 0, non-trivial query,
analyst seems satisfied with the result), offer:

> "Save this query to the knowledge base? I can store it as
> `.claude/db-knowledge/{schema}/_queries/{suggested_name}.sql` with a description."

If the analyst agrees, prompt for a name if not already clear, then write the file:

```sql
-- Name: {query_name}
-- Purpose: {what question this answers}
-- Returns: {grain}
-- Schema: {schema}
-- Dialect: {dialect}
-- Last run: {today's date}
-- Notes: {any gotchas or caveats}

{the SQL}
```

Confirm the save: "Saved to `.claude/db-knowledge/{schema}/_queries/{name}.sql`."

---

## Failure Modes

| Situation | Response |
|-----------|----------|
| Ambiguous intent | Ask one clarifying question before drafting |
| Table not found | List available tables; ask if they meant something else |
| Column not found | Show actual columns on that table; ask for correction |
| Query would scan too much | Show cost estimate; wait for confirmation or offer a narrower query |
| Write intent detected | Redirect to showing affected records instead |
| Execution error | Diagnose and offer a fix; don't retry silently |
| Empty result set | Note it; `db-explain-result` will flag likely causes |
| Salesforce governor limit hit | Explain limit; suggest narrower filters or date pagination |

---

## Notes

- `/db-query` is KB-first: always check gotchas and schema overviews before drafting
- Never hallucinate table names, column names, or data values — if introspection doesn't confirm it, say so
- For Salesforce: use `db-soql` skill for relationship queries; apply `IsDeleted = false` always
- For Athena: always check if target tables are partitioned (`SHOW PARTITIONS`) before querying, and apply partition filters
- The goal is a query the analyst can trust, not just a query that runs
