---
name: db-capture
description: Save a SQL query and its analyst-provided context (purpose, result interpretation, caveats) as a named, annotated .sql file in the knowledge base. Use /db-capture after writing a query you want to keep as a canonical example or reference for the team.
user-invocable: true
argument-hint: "[query_name]"
allowed-tools:
  - Read
  - Write
---

# /db-capture

**Invocation:** `/db-capture [query_name]`

Use this command to save a query to the knowledge base. It captures the SQL, its purpose,
result interpretation, and any caveats as a named, annotated file — making it reusable and
discoverable by future analysts.

No database introspection is required. This command is purely a save operation.

---

## Behavior Overview

1. Identify the query to capture
2. Gather context from the analyst (name, purpose, result summary, caveats)
3. Format the annotated `.sql` file
4. Write to `db-knowledge/{schema}/_queries/{name}.sql`
5. Offer to link in the relevant table's KB entry

---

## Step 1: Identify the Query

If the analyst invoked `/db-capture` immediately after running a query, the query is
the most recent SQL from the conversation. Show it to confirm:
> "Capturing this query:"
> ```sql
> {the query}
> ```
> "Is this the right one? (y / paste a different query)"

If no recent query exists in the conversation, ask:
> "Paste the query you'd like to capture:"

Accept the query as provided. Do not modify or optimize it — capture exactly what the
analyst ran.

---

## Step 2: Determine Target Location

Read `.claude/db-connections/active.yaml` for:
- `type` — database dialect (for the header comment)
- `schema_scope` — the active schema, used to determine the save path

If a `query_name` argument was provided with the invocation, use it (must be kebab-case).

Otherwise, ask:
> "What should this query be named? Use kebab-case (e.g., `active-customers-by-region`)."

Validate the name: only lowercase letters, numbers, and hyphens. If the analyst provides
a name with spaces or underscores, convert it and confirm:
> "Using name: `{converted_name}` — OK?"

Determine the output path: `db-knowledge/{schema}/_queries/{name}.sql`

---

## Step 3: Gather Context

Ask the analyst these questions one at a time, accepting short answers:

**1. Purpose** (required):
> "What does this query answer in one sentence? (e.g., 'Returns active customers with at least one order in the last 90 days')"

**2. Result grain** (required):
> "What does one row in the result represent? (e.g., 'One row per customer')"

**3. Result summary** (optional):
> "What did this query return? Briefly describe the output or key finding. (Press Enter to skip)"

**4. Caveats** (optional):
> "Any caveats, filters, or gotchas the next person should know? (Press Enter to skip)"

**5. Primary tables** (optional — auto-detect if possible):
> "Which tables does this query touch? (comma-separated, or press Enter to detect from the query)"

If the analyst presses Enter to skip an optional question, omit that section from the file.

---

## Step 4: Format the Annotated SQL File

Produce the following file content:

```sql
-- ============================================================
-- {NAME}
-- ============================================================
-- Purpose  : {purpose}
-- Grain    : {result grain — one row per {grain}}
-- Schema   : {schema}
-- Database : {database_type — Snowflake / Oracle / Athena / SQLite / Salesforce}
-- Dialect  : {dialect for syntax reference}
-- Captured : {today's date YYYY-MM-DD}
-- Author   : {analyst name if provided, else "unknown"}
-- Tables   : {comma-separated table list, or "see query"}
-- ============================================================

{If result summary was provided:}
-- Result Summary
-- {result_summary}
--

{If caveats were provided:}
-- Caveats
-- {caveat_1}
-- {caveat_2 if multiple}
--

-- ============================================================

{the raw SQL, exactly as provided — no modifications}
```

Show the formatted file to the analyst:
> "Here's the file that will be saved to `db-knowledge/{schema}/_queries/{name}.sql`:"
> {formatted file content}
> "Save it? (y/n)"

---

## Step 5: Write the File

When the analyst confirms:

1. Check that `db-knowledge/{schema}/_queries/` exists. If not, note:
   > "The `_queries/` directory does not exist yet — it will need to be created at `db-knowledge/{schema}/_queries/`."
   Create it, then proceed.

2. Write the formatted content to the file (UTF-8 encoding).

3. Confirm:
   > "Saved to `db-knowledge/{schema}/_queries/{name}.sql`."

If a file with the same name already exists, warn before overwriting:
> "A file named `{name}.sql` already exists in `_queries/`. Overwrite? (y/n)"

---

## Step 6: Offer to Link in Table Entry

If primary tables were identified (step 3 or auto-detected):

> "Would you like me to add a reference to this query in the KB entry for `{primary_table}`?
> (y/n — I'll add a link under the 'Sample Query' section of `db-knowledge/{schema}/{primary_table}.md`)"

If yes:
- Check whether `db-knowledge/{schema}/{primary_table}.md` exists
- If it exists: find the `## Sample Query` section and append a reference:
  ```markdown
  - See [`{name}.sql`](_queries/{name}.sql) — {purpose (first sentence)}
  ```
- If it doesn't exist: tell the user:
  > "No KB entry found for `{primary_table}` yet. Run `/db-document {primary_table}` to create one, and this query will be linked automatically."

If no, skip silently.

---

## Notes

- This command intentionally has no cost gate — it performs no database queries
- The captured SQL is never validated or executed by this command; it is saved as-is
- If the analyst is mid-session and already captured context about the query (e.g., via `/db-explain query:`), offer to use that context to pre-fill the purpose and caveats fields
- For Salesforce SOQL queries, the file extension is still `.sql` — note in the header comment that it is SOQL dialect
- Query names should be descriptive enough to be scannable in a directory listing (prefer `active-orders-by-region` over `query1`)
