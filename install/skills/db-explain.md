---
name: db-explain
description: Plain-English explanation of a database table, view, column, or SQL query. Synthesizes schema metadata, sample data, and knowledge base entries into a concise explanation with inferred grain, business purpose, and gotchas. Offers to save the explanation to the knowledge base.
---

# /db-explain

**Invocation:** `/db-explain {target}`

**Target forms:**
- `/db-explain {table_name}` — explain a table or view
- `/db-explain column: {table_name}.{column_name}` — explain a specific column
- `/db-explain query: {SQL}` — explain a SQL query in plain English

Use this command when an analyst wants to understand what a database object is,
what it represents, and how to use it safely.

---

## Behavior Overview

1. Parse the target argument
2. Check the knowledge base for an existing entry
3. Run introspection and sampling
4. Synthesize a plain-English explanation
5. Offer to save to the knowledge base

---

## Step 1: Parse the Target

Determine what the analyst is asking about:

| Input form | Object type | Action |
|------------|-------------|--------|
| `sales_orders` | Table or view | Introspect + sample the whole table |
| `column: sales_orders.status` | Column | Introspect + sample the parent table; focus on the named column |
| `query: SELECT ...` | SQL query | Explain the query structure; do not execute it unless explicitly asked |

If no target is provided after `/db-explain`, ask:
> "What would you like me to explain? You can provide a table name, `column: table.column`, or `query: <SQL>`."

---

## Step 2: Check the Knowledge Base

Before running any database queries, check `.claude/db-knowledge/` for existing documentation:

- For a table: look for `.claude/db-knowledge/{schema}/{table_name}.md`
- For a column: look for `.claude/db-knowledge/{schema}/{table_name}.md` and scan for a column entry
- For a query: no KB check needed

If an existing KB entry is found:
> "Found an existing knowledge base entry for `{table_name}`. I'll use it as context and supplement with fresh introspection."

Cite the KB entry in your explanation (e.g., "Per the knowledge base: …"). If the KB entry conflicts with what introspection reveals, flag the discrepancy:
> "⚠️ Note: The knowledge base entry says X, but current introspection shows Y. The KB may be outdated."

---

## Step 3: Run Introspection and Sampling

### For a table or view:
1. Run `db-introspect` on the target table
2. Run `db-sample` (10 rows) on the target table

### For a column:
1. Run `db-introspect` on the parent table (you need the full column list for context)
2. Run `db-sample` (10 rows) on the parent table
3. Run per-column value summary stats on the named column specifically:

```sql
-- SQLite / Oracle / Athena (adapt syntax per dialect)
SELECT
  COUNT(*) AS total_rows,
  COUNT({column}) AS non_null_count,
  COUNT(DISTINCT {column}) AS distinct_count,
  MIN({column}) AS min_val,
  MAX({column}) AS max_val
FROM {table};
```

For columns with low distinct counts (≤20), also fetch the top values:
```sql
-- SQLite / Oracle
SELECT {column}, COUNT(*) AS occurrences
FROM {table}
GROUP BY {column}
ORDER BY COUNT(*) DESC
LIMIT 20;

-- Athena
SELECT {column}, COUNT(*) AS occurrences
FROM {database}.{table}
GROUP BY {column}
ORDER BY COUNT(*) DESC
LIMIT 20;
```

### For a query:
- Do not execute the query unless the analyst explicitly asks
- Read the query text and introspect the tables it references (from `db-introspect`)
- Use the column definitions from introspection to interpret what the query is doing

---

## Step 4: Synthesize the Explanation

### Table Explanation

Produce a structured explanation:

```
## {TABLE_NAME} — What It Is

**Type:** {Table | View}
**Rows (approx):** {N}
**Grain:** One row per {inferred grain — e.g., "order", "customer", "daily snapshot per product"}

### Business Purpose
{2–4 sentences answering: What real-world thing does each row represent? Who created
this data and when? What decisions or reports does it support? What system or process
produces it?}

### Key Columns
{Describe the 4–8 most meaningful columns. For each: what it contains, its data type,
whether it is required, and any business rules or patterns observed in the sample.}

| Column | Meaning | Notes |
|--------|---------|-------|
| `{col}` | {plain English description} | {PK / FK / soft delete / status code / etc.} |

### Observed Patterns
{Based on sample + introspection, note any of the following if present:}
- **Soft delete:** `{col}` is NULL for active rows; set when deleted. Always filter `WHERE {col} IS NULL`.
- **Status codes:** `{col}` takes values {list from sample}. Meaning: {infer if possible, or note "confirm with data owner"}.
- **Snapshot values:** `{col}` captures the value at transaction time, not current state.
- **Active flag:** `{col} = 1` means active; `{col} = 0` means inactive or terminated.
- **High null rate:** `{col}` is {N}% null — treat as optional or legacy.
- **Append-only:** No update timestamps detected; rows appear to be inserted and never modified.

### Relationships
{List FK relationships to/from this table. For each, note the join type and any pre-filter requirements.}
- `{this_table}.{fk_col}` → `{ref_table}.{ref_col}` — {brief description of what the join represents}

### Gotchas
{Known issues. Pull from KB if available; also infer from sample and introspection.}
- {Gotcha 1}
- {Gotcha 2}

If no gotchas are found: "No gotchas identified from introspection. Add entries here as you discover them."
```

---

### Column Explanation

```
## {TABLE_NAME}.{COLUMN_NAME} — What It Is

**Type:** {data type}
**Nullable:** {Yes / No}
**Part of PK:** {Yes / No}
**Foreign key:** {→ ref_table.ref_col, or No}

### What It Contains
{2–3 sentences: What does this column store? What is the unit, format, or coding scheme?
What business concept does it represent?}

### Value Profile (full table)
- **Non-null:** {N} / {total} ({pct}%)
- **Distinct values:** {N}
- **Range:** {min} to {max}

{If distinct count ≤ 20, show value distribution:}
| Value | Count |
|-------|-------|
| {val} | {N}   |

### Interpretation
{Plain-English interpretation of what the values mean. For status codes: interpret each
known value. For timestamps: explain what event they mark. For IDs: explain what entity
they reference. For amounts: note currency/unit assumptions.}

### Gotchas
- {e.g., "NULL means the record predates the system — not that the value is unknown"}
- {e.g., "Value '0' and NULL are used interchangeably in legacy data before 2022"}
```

---

### Query Explanation

```
## Query Explanation

### What This Query Does
{1–3 sentences: plain English summary of what the query is computing and what result it produces.}

### Result Grain
One row per {inferred grain from GROUP BY, DISTINCT, or join structure}.

### Walk-Through
{Step through the query structure:}
1. **FROM / JOINs:** {Which tables, what relationships, any pre-filters on joins}
2. **WHERE clause:** {What rows are included/excluded, any gotcha filters detected}
3. **GROUP BY / aggregates:** {What is being summarized and how}
4. **ORDER BY / LIMIT:** {How results are sorted and bounded}

### Risks and Caveats
{Flag any of the following if detected:}
- ⚠️ **Missing soft-delete filter** — `{table}` has a `{deleted_col}` column but the query doesn't filter on it. Results may include deleted records.
- ⚠️ **Fan-out risk** — Joining `{left}` to `{right}` on `{key}` where `{right}` has multiple rows per `{key}`. Aggregates may be inflated.
- ⚠️ **Full table scan** — No WHERE clause on `{table}`. On large tables this may be expensive.
- ⚠️ **SELECT \*** — Fetches all columns; consider selecting only needed columns on columnar databases (Athena, Snowflake).
- ⚠️ **Date filter missing** — Query touches a time-series table without a date range filter.

If no risks detected: "No obvious risks detected in this query."
```

---

## Step 5: Offer to Save

After explaining a **table or view**, always ask:

> "Save this explanation to `.claude/db-knowledge/{schema}/{table_name}.md`? (y/n)"

If yes:
1. Create `.claude/db-knowledge/{schema}/` directory if it doesn't exist
2. Write the explanation (formatted as a KB entry — see format below) to `{table_name}.md`
3. Confirm: "Saved to `.claude/db-knowledge/{schema}/{table_name}.md`."

After explaining a **column**, ask:
> "Add this column explanation to `.claude/db-knowledge/{schema}/{table_name}.md`? (y/n)"

If yes:
- If the file exists, append the column entry to the appropriate section
- If the file doesn't exist, create a new entry with the table header and column entry
- Confirm save

**Do not offer to save query explanations** — they are transient by nature. The analyst can use `/db-capture` to save notable queries explicitly.

---

### KB Entry Format (for saved table explanations)

```markdown
# {TABLE_NAME}

**Schema:** `{schema}`
**Type:** {Table | View}
**Last Updated:** {today's date}
**Rows (approx):** {N}
**Grain:** One row per {grain}

## Business Purpose

{2–4 sentence description}

## Key Columns

| Column | Type | Meaning | Notes |
|--------|------|---------|-------|
| `col1` | INTEGER | {description} | PK |
| `col2` | TEXT | {description} | |

## Gotchas

- {gotcha 1}
- {gotcha 2}

## Open Questions

- [ ] {any unresolved question discovered during explanation}
```

---

## Notes

- Infer grain from: table name suffix (orders vs order_items), PK structure, row count relative to related tables, and sample data
- If you cannot confidently infer the grain, say: "Grain unclear — likely one row per {best guess}, but confirm with the data owner"
- Do not make up business context — state what is observable and flag what needs confirmation
- Column explanations are more valuable when they include the value distribution for low-cardinality columns (status, type, category fields)
