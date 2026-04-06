---
name: db-orient
description: Structured orientation to an unfamiliar database schema. Run /db-orient when you are new to a database and want a complete picture: entity clusters, key relationships, gotchas, and common query patterns. Produces output that can be saved to the knowledge base as _schema-overview.md.
---

# /db-orient

**Invocation:** `/db-orient [schema_name]`

Use this command when an analyst is new to a database and needs a structured orientation.
It synthesizes introspection metadata and sample data into a navigable overview matching
the format of `_schema-overview-template.md`.

---

## Behavior Overview

1. Load connection context
2. Run `db-introspect` across all tables and views in scope
3. Run `db-sample` on up to 5 representative tables
4. Check for an existing `_schema-overview.md` in the KB
5. Synthesize and present the orientation
6. Offer to save to the knowledge base

---

## Step 1: Load Connection Context

Read `.claude/db-connections/active.yaml`. Identify:
- Active connection name and type
- `schema_scope` — the schemas/databases to orient to
- If `schema_name` was passed as an argument, use it; otherwise use the first entry in `schema_scope` (or the SQLite file path's implied schema)

If `active.yaml` does not exist, stop and tell the user:
> "No active connection found. Please copy `templates/connections.example.yaml` to `.claude/db-connections/active.yaml` and configure your connection."

---

## Step 2: Run Introspection

Use the `db-introspect` skill to extract metadata for all tables and views in the target schema.

Tell the user what you are doing:
> "Introspecting {display_name} — reading table structure, columns, and relationships…"

Collect from introspection output:
- All table names and row counts
- All column definitions (name, type, nullable, PK, FK, comment)
- All FK relationships between tables
- Any table-level or column-level comments

---

## Step 3: Sample Representative Tables

Use the `db-sample` skill on up to **5 tables**, selected as follows:
1. Prefer tables with the highest row counts (they are likely the core fact/event tables)
2. If row counts are unavailable or equal, sample the first 5 alphabetically
3. Always sample tables that appear as the target of FK references (the "lookup" side)

Tell the user:
> "Sampling {N} tables to understand data shape and patterns…"

Use the sample output to:
- Identify soft-delete patterns (`deleted_at`, `is_active`, `is_deleted`, `status` columns with 'D'/'deleted' values)
- Identify snapshot columns (`*_at_order`, `*_price_snapshot`, `unit_price` in line-item tables)
- Identify timestamp and date column formats
- Note any columns with suspiciously high null rates (>50%)

---

## Step 4: Check Existing KB Entry

Check whether `.claude/db-knowledge/{schema}/_schema-overview.md` exists.

If it exists:
> "Note: A schema overview already exists in the knowledge base (last updated: {date from file header, or unknown}).
> Showing a fresh orientation from live introspection. I'll ask whether to update the KB file at the end."

If it does not exist: proceed silently.

---

## Step 5: Synthesize Orientation

Produce a structured orientation document following the `_schema-overview-template.md` format.
Include all sections below. Fill in what you can from introspection; note gaps explicitly rather than omitting sections.

---

### Output Structure

```markdown
# {SCHEMA_NAME} — Schema Overview

**Database:** {database type}
**Schema / Dataset:** `{schema_name}`
**Last Updated:** {today's date}
**Status:** Draft

---

## 1. Domain Summary

{2–4 sentences synthesizing the apparent business domain from table names, column names,
and comments. Answer: What does this schema serve? What systems likely produce it?}

**Business Domain:** {inferred domain — e.g., Order Management, HR, Customer Identity}
**Source System(s):** {inferred or "unknown — check with data owner"}
**Primary Consumers:** {inferred from table purpose, or "unknown"}
**Refresh Cadence:** {from timestamp columns if detectable, or "unknown"}

---

## 2. Entity Clusters

{Group tables by business concept. Use table name prefixes (hr_, sales_, dim_, fact_)
and FK relationships as clustering signals. Aim for 2–5 clusters.
A table can appear in multiple clusters if it bridges domains.}

### Cluster A: {Name}

> {One sentence describing this cluster.}

| Table | Role | Notes |
|-------|------|-------|
| `table_a` | Core entity | {key observation — PK, row count, notable columns} |
| `table_b` | Detail / attributes | |

---

### Cluster B: {Name}

> {One sentence.}

| Table | Role | Notes |
|-------|------|-------|
| `table_c` | {role} | |

---

## 3. Key Relationships

{Document each FK relationship found. Use the structured format below.
Prioritize: FKs with fan-out risk, FKs where key names differ between tables,
and any join path that requires a filter prerequisite.}

### {LEFT_TABLE} → {RIGHT_TABLE}

\`\`\`
Cardinality : {1:1 | 1:many | many:1 | many:many}
Join type   : {INNER | LEFT | Depends — see notes}
Left key    : {left_table}.{left_key_column}
Right key   : {right_table}.{right_key_column}
Direction   : {Preferred: left → right | Bidirectional}
Pre-filters : {Filters required before joining, or "None required"}
Gotchas     : {Known issues, or "None known"}
\`\`\`

\`\`\`sql
-- Example
SELECT l.{key}, r.{col}
FROM {left_table} l
{INNER|LEFT} JOIN {right_table} r
    ON l.{left_key} = r.{right_key}
{WHERE filter if required}
\`\`\`

---

## 4. Relationship Map (Summary Table)

| From Table | To Table | Via | Cardinality | Join Type | Pre-filter? | Risk |
|------------|----------|-----|-------------|-----------|-------------|------|
| `table_a`  | `table_b` | `table_a.id = table_b.a_id` | 1:many | LEFT | No | Low |

---

## 5. Common Query Patterns

{Provide 2–3 starter queries covering the most common analyst use cases inferred from
the schema. Label each with its purpose and result grain.}

### Pattern 1: {Descriptive Name}

\`\`\`sql
-- Purpose: {What question does this answer?}
-- Returns: {One row per what?}
-- Notes:   {Any caveats}

SELECT ...
FROM {table}
WHERE ...
\`\`\`

---

## 6. Schema-Level Gotchas

{Detected patterns that every analyst using this schema should know.
Check for these signals in introspection + sample output:}

{For each detected gotcha, add a bullet:}
- ⚠️ **{TABLE} uses soft deletes** — filter `WHERE {deleted_col} IS NULL` (or `= 0` / `= 1`)
  before joining or aggregating. Rows without this filter include {churned/deleted} records.
- ⚠️ **Inconsistent soft-delete patterns** — {TABLE_A} uses `deleted_at` but {TABLE_B}
  uses `is_active = 0`. Apply the right filter per table.
- ⚠️ **{TABLE}.{col} is a snapshot value** — it captures the value at transaction time,
  not the current value. Join to the source table only if you want current values.
- ⚠️ **{TABLE} has {N}% null rate on {col}** — this column is not reliably populated.
  Use `COUNT({col})` rather than `COUNT(*)` when aggregating it.

If no gotchas are detected, write: "No schema-level gotchas detected from introspection.
Add entries here as you discover them."
```

---

## Gotcha Detection Signals

Scan column names across all tables for these patterns:

| Pattern | Signal | Gotcha to flag |
|---------|--------|----------------|
| Column named `deleted_at`, `is_deleted`, `deleted_flag` | Soft delete | Filter required |
| Column named `is_active`, `active_flag` | Active/inactive rows | Filter required |
| Column named `status` with values like 'D', 'deleted', 'INACTIVE' | Status-based exclusion | Filter required |
| `*_at_order`, `*_snapshot`, `unit_price` in a line-item table | Snapshot pricing | Don't join for current price |
| Tables with same FK target but different column names | Inconsistent key naming | Note alias |
| Two tables with same business concept but different soft-delete patterns | Inconsistent patterns | Call it out explicitly |
| Column with >50% nulls in sample | Sparse column | Flag for COUNT() usage |

---

## Step 6: Offer to Save

After presenting the orientation, always ask:

> "Save this orientation to `.claude/db-knowledge/{schema}/_schema-overview.md`?
> (y = save, n = skip, update = overwrite existing)"

If the user says **y** or **save**:
1. Create the directory `.claude/db-knowledge/{schema}/` if it does not exist
2. Write the orientation document (without the markdown code fence wrappers) to `_schema-overview.md`
3. Confirm: "Saved to `.claude/db-knowledge/{schema}/_schema-overview.md`."

If an existing file was present and the user says **update**:
- Overwrite the existing file
- Confirm: "Updated `.claude/db-knowledge/{schema}/_schema-overview.md`."

If the user says **n** or **skip**: acknowledge and move on.

---

## Notes

- Do not speculate beyond what introspection and samples support — use "unknown" or "inferred from column names" to be transparent
- If the schema has more than 30 tables, group them into clusters first and sample only from the top 5 by row count; offer to drill into specific clusters on request
- The orientation is a starting point, not a final document — the analyst should review and correct it before sharing with the team
- Snowflake and Salesforce are not yet fully supported — acknowledge the connection type and offer to proceed with manual guidance if the user wants to explore ahead of Sprint 3
