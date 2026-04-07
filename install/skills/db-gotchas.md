---
name: db-gotchas
description: Surface known issues and traps for a table or schema from the knowledge base. Falls back to live introspection-based gotcha detection if the KB has no entries for the target. Always KB-first — no database calls if documented gotchas exist.
---

# /db-gotchas

**Invocation:**
- `/db-gotchas` — show all known gotchas for the active connection's schema(s)
- `/db-gotchas {table_name}` — show gotchas for a specific table
- `/db-gotchas {schema_name}` — show gotchas for a specific schema

Use this command before writing queries against unfamiliar tables, or when something in
a query result looks wrong and you want to check for known data quality issues.

---

## Behavior Overview

1. Parse the target (table, schema, or all)
2. Load gotchas from the knowledge base (always try KB first — no DB call needed)
3. If KB has no entries for the target: fall back to live introspection
4. Present gotchas grouped by scope
5. Offer to save any inferred gotchas if analyst confirms them

---

## Step 1: Parse the Target

| Input | Scope |
|-------|-------|
| No argument | All gotchas for all schemas in the active connection |
| `{table_name}` | Gotchas specific to that table + parent schema gotchas |
| `{schema_name}` | All gotchas for that schema (schema-level + all table entries) |

If the target is ambiguous (same name exists in multiple schemas), ask:
> "Did you mean `{schema_a}.{name}` or `{schema_b}.{name}`?"

---

## Step 2: Load from Knowledge Base (Always First)

Check the following KB locations. For a table target, check all three. For schema target,
check the first two. For no argument, scan all.

### 2a. Cross-schema gotchas
File: `.claude/db-knowledge/_gotchas.md`

Load and filter for entries that mention the target table or schema, or that are marked
as "Affects: All" / cross-schema.

### 2b. Schema-level gotchas
File: `.claude/db-knowledge/{schema}/_schema-overview.md`, **Section 6 (Schema-Level Gotchas)**

If the file exists, extract the gotcha list from Section 6.

### 2c. Per-table gotchas
File: `.claude/db-knowledge/{schema}/{table_name}.md`, **Gotchas section**

If the file exists, extract the Gotchas section.

---

## Step 3: Present KB Gotchas

If KB entries were found, present them grouped by scope:

```
## Gotchas for {target}

### Cross-schema
{entries from _gotchas.md, if relevant}

### Schema: {schema_name}
{entries from _schema-overview.md Section 6, if present}

### Table: {table_name}
{entries from {table}.md Gotchas section, if present}

─────────────────────────────
Source: knowledge base (no DB query needed)
Last updated: {date from file header, or "unknown"}
```

If no relevant KB entries exist for any scope, proceed to Step 4 (live fallback).

If **some but not all** scopes have KB entries, show what exists and note what's missing:
> "No KB entries found for `{table_name}` specifically. Showing schema-level gotchas — run live introspection to check for table-specific patterns?"

---

## Step 4: Live Fallback (KB Empty for Target)

If the KB has **no gotcha entries for the target table or schema**, offer to detect them
from live introspection:

> "No gotchas documented for `{target}` yet. I can run a quick introspection and sample to
> look for common patterns (soft deletes, status codes, snapshot columns, high nulls).
> Run live check? (y/n)"

If the analyst says yes:

### 4a. Run introspection
Use `db-introspect` on the target table (or all tables in the schema).

### 4b. Run sample
Use `db-sample` on the target table (10 rows).

### 4c. Apply gotcha detection signals

Scan introspection + sample output for these patterns:

| Signal | What to look for | Gotcha to flag |
|--------|-----------------|----------------|
| Soft delete — null sentinel | Columns named `deleted_at`, `removed_at` with NULL for active rows | Filter `WHERE {col} IS NULL` required |
| Soft delete — boolean flag | Columns named `is_deleted`, `is_active`, `active_flag`, `deleted_flag` | Filter `WHERE {col} = 0` or `= 1` required |
| Soft delete — status encoding | Column named `status` with values in sample that include 'D', 'DELETED', 'deleted', 'INACTIVE' | Filter `WHERE status != 'D'` required |
| Salesforce soft delete | `IsDeleted` column | Filter `WHERE IsDeleted = false` always required |
| Snapshot / denormalized values | Columns named `*_at_order`, `*_snapshot`, `*_price_captured`, `unit_price` in a line-item table | Value frozen at transaction time — don't join for current value |
| High null rate | Column with > 50% NULL in sample | Flag for `COUNT({col})` rather than `COUNT(*)` |
| Status codes — unknown | Column named `type`, `status`, `category`, `code` with short-string values in sample not explained in comments | Values may be undocumented — flag as open question |
| Inconsistent patterns across tables | Two tables with same business concept using different soft-delete patterns | Call out explicitly — easy to get wrong |
| Timestamp column format | Mixed formats in sample (some ISO, some epoch, some locale-formatted strings) | Conversion required before date math |

### 4d. Present inferred gotchas

Clearly mark these as inferred, not confirmed:

```
## Inferred Gotchas for {target_table}

The following patterns were detected from live introspection and a 10-row sample.
These are **not yet confirmed** — verify with the data owner before relying on them.

- ⚠️ **Soft delete detected** — `{col}` is NULL for active rows. Always filter
  `WHERE {col} IS NULL` before querying or joining.
  *Inferred from: column name pattern + sample shows mix of NULL and non-NULL values*

- ⚠️ **Status code `{col}`** — values observed: {list from sample}. Meaning unknown.
  Add to open questions if business rules aren't documented.
  *Inferred from: low-cardinality string column, values not explained in schema comments*

─────────────────────────────
Source: live introspection (not yet in KB)
```

### 4e. Offer to save

After presenting inferred gotchas, ask:

> "Add confirmed gotchas to the knowledge base? I can append them to
> `.claude/db-knowledge/_gotchas.md` or `.claude/db-knowledge/{schema}/{table}.md`.
> Which ones are correct? (list numbers, or 'all' / 'none')"

If the analyst confirms one or more:
- For table-specific gotchas: append to `.claude/db-knowledge/{schema}/{table}.md`
  under the **Gotchas** section (create the file if it doesn't exist using the KB entry format)
- For cross-schema patterns: append to `.claude/db-knowledge/_gotchas.md`
- Confirm: "Saved {N} gotcha(s) to the knowledge base."

---

## Output Format (KB Entries Present)

Present each gotcha in the format it appears in the KB. If the KB uses the standard format,
it will look like:

```
### ⚠️ [SHORT TITLE]
**Affects:** {tables or schemas}
**Impact:** {what goes wrong if you don't know}
**Fix:** {how to work around}
**Discovered:** {date}
```

Preserve this format exactly when surfacing from the KB — don't paraphrase or summarize
individual gotchas, as the analyst may be reading them carefully.

---

## Notes

- `/db-gotchas` is read-only from the KB — no database queries unless the fallback is triggered
- If the KB has both schema-level and table-level entries, show both — they complement each other
- The inferred fallback is useful for analysts who have just run `/db-orient` and want a quick sanity check before writing queries on newly introspected tables
- If the analyst asks "any gotchas I should know before writing this query?" during a `/db-query` workflow, this skill's behavior is already embedded in Step 2 of `/db-query` — no need to run `/db-gotchas` separately
