---
name: db-doc-writer
description: Documentation writing engine for the DB Analyst Framework. Given introspection output and analyst-provided context, drafts a structured per-table KB entry in the standard {table}.md format. Used internally by /db-document. Can also be invoked directly when you already have introspection output and want to produce a draft without re-querying.
---

# DB Doc Writer Skill

Use this skill to draft a knowledge base entry for a database table. It takes introspection
results and analyst context as input and produces a complete `{table}.md` file ready for
review and saving.

This is the writing engine — it does not query the database itself. Always run `db-introspect`
and optionally `db-sample` first, then pass those results into this skill.

---

## Inputs

- **Table name** — the table being documented
- **Schema name** — the schema or owner
- **Database type** — dialect (sqlite, oracle, athena, snowflake, salesforce)
- **Introspection output** — result of the `db-introspect` skill for this table (columns, types, nullability, PKs, FKs, row count, comments)
- **Sample output** — result of `db-sample` on this table (optional but recommended)
- **Analyst context** — anything the analyst has shared: known purpose, grain, gotchas, business rules (optional)
- **Existing KB entry** — contents of the current `{table}.md` if one exists (optional — triggers update mode)
- **Emit frontmatter** — whether to include YAML frontmatter (read from `active.yaml` field `emit_frontmatter: true`, default false)

---

## Step 1: Determine Mode

**Fresh draft:** No existing KB entry provided. Build from scratch using introspection + sample + analyst context.

**Update mode:** An existing KB entry was provided. Open with:
> "I found an existing KB entry for `{table_name}`. I'll show you what's changed since it was written and propose an updated draft."
>
> Produce a diff summary: columns added, columns removed, type changes, new FK relationships, row count change if significant. Then produce the full updated draft incorporating both the existing content and the changes.

---

## Step 2: Draft the KB Entry

Produce the full structured entry in the format below. For every section:
- Fill in what you can confidently determine from introspection and sample
- Mark uncertain items with `{inferred — confirm with data owner}` rather than omitting them
- Pull exact wording from analyst context where provided; do not paraphrase it

### Frontmatter (only if `emit_frontmatter` is true)

```yaml
---
schema: {schema_name}
database: {database_type}
grain: {one-sentence grain statement, or "unknown — see Grain section"}
status: draft
last_documented: {today's date YYYY-MM-DD}
documented_by: {analyst name if provided, else "introspection"}
---
```

### Entry Body

```markdown
# {TABLE_NAME}

**Schema:** `{schema_name}`
**Database:** {database_type — Snowflake / Oracle / Athena / SQLite / Salesforce}
**Last Documented:** {today's date YYYY-MM-DD}
**Documented By:** {analyst name if provided, else "introspection"}
**Rows (approx):** {row_count, or "unknown"}

## Purpose

{2–4 sentences answering: What real-world thing does each row represent? What system or
process produces this data? Who or what consumes it? If purpose cannot be determined from
introspection alone, write the best inference followed by "— confirm with data owner."}

## Grain

One row per {grain statement}.

{If grain is uncertain, explain the ambiguity: "Grain unclear — likely one row per
{best guess} based on {reasoning}, but the presence of {columns} suggests it may be
{alternative grain}. Confirm before aggregating."}

## Key Fields

| Column | Type | Nullable | Description | Notes |
|--------|------|----------|-------------|-------|
{For each column from introspection output, one row:
| `col_name` | data_type | Yes/No | {column comment if present, else inferred description} | {PK / FK→table.col / soft delete / status / snapshot / index} |}

{Guidelines for the Notes column:
- PK columns: mark "Primary key"
- FK columns: mark "→ {ref_table}.{ref_col}"
- Columns likely used as soft-delete flags: mark "Soft delete — see Gotchas"
- Columns with status codes: mark "Status — see Gotchas"
- Snapshot/price columns in line-item tables: mark "Snapshot value at transaction time"
- Columns with >50% nulls in sample: mark "Sparsely populated ({N}% null)"
- Indexed columns: mark "Indexed"}

## Relationships

{For each FK relationship found in introspection, one bullet:}
- `{this_table}.{fk_col}` → `{ref_schema}.{ref_table}.{ref_col}` ({cardinality: many-to-one | one-to-many | one-to-one})

{For each inbound FK (other tables pointing to this one), one bullet:}
- ← `{other_table}.{fk_col}` references this table ({cardinality})

{If no FK relationships exist: "No FK relationships detected from introspection. Add manually if known."}

## Gotchas

{For each detected gotcha pattern, add a bullet:}
- ⚠️ **Soft delete** — `{col}` is not NULL / = 0 / = 'D' for deleted rows. Always filter
  `WHERE {col} IS NULL` (or the appropriate value) before joining or aggregating.
- ⚠️ **Status codes** — `{col}` uses coded values: {list observed values}. Meaning of each:
  {infer if possible; flag unknowns as "confirm with data owner"}.
- ⚠️ **Snapshot value** — `{col}` captures the value at the time of the transaction, not
  the current value. Do not join to {source_table} expecting to get current state.
- ⚠️ **Sparse column** — `{col}` is {N}% null. Use `COUNT({col})` not `COUNT(*)` when
  aggregating this field.
- ⚠️ **Timezone** — `{col}` appears to be stored in UTC based on value range. Convert for
  local reporting.

{If no gotchas are detected: "No gotchas identified from introspection. Add entries as you discover them."}

{If analyst provided gotchas in context: include them verbatim, marked with their source:
"(per analyst)" or "(per knowledge base)"}

## Sample Query

```sql
-- {Brief description of what this query returns}
-- Returns: one row per {grain}
SELECT {list the 4–6 most meaningful columns, not SELECT *}
FROM {schema}.{table_name}
{WHERE clause incorporating any detected soft-delete or active-only filter}
LIMIT 20;
```

{If FK relationships exist, add a second pattern showing the most natural join:}
```sql
-- {Brief description}
SELECT {key columns from both tables}
FROM {schema}.{table_name} t
{INNER|LEFT} JOIN {schema}.{ref_table} r
    ON t.{fk_col} = r.{ref_col}
{WHERE filters}
LIMIT 20;
```

## Open Questions

- [ ] {Any column or pattern that needs confirmation from data owner}
{If analyst provided open questions, include them here.}
{If nothing is unclear: remove this section or leave as "None — documentation appears complete."}
```

---

## Step 3: Gotcha Detection

Before drafting, scan the introspection and sample output for these signals and populate
the Gotchas section accordingly:

| Signal | What to flag |
|--------|--------------|
| Column named `deleted_at`, `is_deleted`, `deleted_flag`, `is_active`, `active_flag` | Soft delete — filter required |
| Column named `status` with low-cardinality values including 'D', 'deleted', 'INACTIVE', '0' | Status-based exclusion |
| Column named `*_at_order`, `*_snapshot`, `unit_price` in line-item context | Snapshot value at transaction time |
| Timestamp column values all in UTC range | UTC storage — local conversion needed |
| Column with >50% null rate in sample | Sparse column — COUNT() caution |
| Column with inconsistent naming relative to FK target (e.g., `cust_id` vs `customer_id`) | Note the alias |
| Salesforce `IsDeleted = true` pattern | Filter required |

---

## Step 4: Return Draft for Review

Present the complete draft to the analyst. Do **not** write any file yet.

Say:
> "Here's the draft KB entry for `{table_name}`. Review it and let me know:
> - Any corrections (wrong grain, wrong description, inaccurate gotchas)
> - Any additions (gotchas you know, relationships not in the schema, open questions)
> - Type **save** when ready to write, or **discard** to cancel."

If the analyst makes corrections, apply them inline and re-present the relevant section.
When the analyst says **save**, hand control back to the calling skill (e.g., `/db-document`)
to perform the actual file write.

---

## Notes

- The quality of the draft scales with introspection quality — if column comments exist in the database, they produce far better Key Fields entries
- Do not fabricate business context. If you cannot infer grain or purpose confidently, say so explicitly — a draft with honest uncertainty is more useful than a confident wrong one
- For SQLite, note in the Purpose section that SQLite lacks column comments; descriptions are inferred from names and sample data only
- For Salesforce, replace the schema/table naming convention with object API name notation (e.g., `Account`, `Opportunity__c`) and note that `IsDeleted` is a standard Salesforce pattern
- The `emit_frontmatter` flag enables Obsidian Dataview compatibility but is not required for the core KB to function
