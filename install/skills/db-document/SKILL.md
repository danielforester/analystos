---
name: db-document
description: Interactive table documentation workflow. Runs introspection on a target table, checks for an existing KB entry, drafts a structured data dictionary entry via db-doc-writer, accepts analyst review and corrections, and writes the final entry to db-knowledge/{schema}/{table}.md. Use /db-document when you want to create or update a table's KB entry.
user-invocable: true
argument-hint: "<table_name> [schema_name]"
allowed-tools:
  - Read
  - Bash
  - Write
  - Edit
---

# /db-document

**Invocation:** `/db-document {table_name} [schema_name]`

Use this command to create or update a knowledge base entry for a database table.
It orchestrates introspection, draft generation via `db-doc-writer`, analyst review,
and file writing in a single interactive flow.

---

## Behavior Overview

1. Load connection context
2. Run introspection and sampling on the target table
3. Check for an existing KB entry
4. Draft the entry with `db-doc-writer`
5. Present draft for analyst review
6. Write the final entry on approval
7. Offer to update the KB index

---

## Step 1: Load Connection Context

Read `.claude/db-connections/active.yaml`. Identify:
- `type` — database dialect
- `schema_scope` — active schema(s) to qualify the table name
- `emit_frontmatter` — whether to include YAML frontmatter in KB entries (default: false)

If `schema_name` was passed as an argument, use it. Otherwise use the first entry in
`schema_scope` (or the SQLite-implied schema `main`).

If `active.yaml` does not exist, stop and tell the user:
> "No active connection found. Please copy `templates/connections.example.yaml` to `.claude/db-connections/active.yaml` and configure your connection."

If no `table_name` argument was provided, ask:
> "Which table would you like to document? (e.g., `/db-document orders`)"

---

## Step 2: Run Introspection and Sampling

Apply the `db-introspect` skill on the target table.

Tell the user:
> "Introspecting `{schema}.{table_name}`…"

Then apply the `db-sample` skill (10 rows) on the same table.

Tell the user:
> "Sampling `{table_name}` to detect patterns and gotchas…"

If introspection fails (table not found, permission denied), stop and report:
> "Could not introspect `{table_name}`: {error}. Check that the table exists and the connection has SELECT access."

---

## Step 3: Check for Existing KB Entry

Check whether `db-knowledge/{schema}/{table_name}.md` exists.

**If the file exists:**
- Read its contents
- Tell the user:
  > "Found an existing KB entry for `{table_name}` (last documented: {date from file header, or 'unknown'}). I'll show you what's changed and propose an updated draft."
- Pass the existing entry to `db-doc-writer` as the `existing_kb_entry` input (triggers update mode)

**If the file does not exist:**
- Proceed silently in fresh draft mode

---

## Step 4: Generate Draft

Apply the `db-doc-writer` skill with all collected inputs:
- Table name and schema
- Database type (from `active.yaml`)
- Introspection output (from Step 2)
- Sample output (from Step 2)
- Existing KB entry if found (from Step 3)
- `emit_frontmatter` flag (from `active.yaml`)

The `db-doc-writer` skill handles draft generation, gotcha detection, and presenting
the draft for review. Follow its output instructions — present the draft and wait for
analyst feedback.

---

## Step 5: Accept Analyst Corrections

While the analyst reviews the draft, accept these inputs:

| Analyst input | Action |
|---------------|--------|
| Correction to a specific section | Apply the correction and re-show the affected section |
| New gotcha | Add to Gotchas section |
| New open question | Add to Open Questions section |
| Grain clarification | Update Grain section |
| Purpose rewrite | Replace Purpose section with analyst's text |
| **`save`** | Proceed to Step 6 |
| **`discard`** | Cancel — confirm: "Discarded. No file was written." |
| **`preview`** | Show the current full draft again |

After each correction, confirm the change:
> "Updated {section name}. Type `save` when ready, or continue making changes."

---

## Step 6: Write the Final Entry

When the analyst types **save**:

1. Determine the output path: `db-knowledge/{schema}/{table_name}.md`
2. If the directory `db-knowledge/{schema}/` does not exist, note that it needs to be created
3. Write the approved draft to the file (UTF-8 encoding)
4. Confirm:
   > "Saved to `db-knowledge/{schema}/{table_name}.md`."

If overwriting an existing entry, confirm with one extra line:
   > "(Previous entry overwritten. Use `git diff` to review changes.)"

---

## Step 7: Offer to Update the KB Index

After a successful save, ask:
> "Update the knowledge base index (`db-knowledge/README.md`) to include this entry? (y/n)"

If yes, invoke the `db-index` skill.

If no, acknowledge:
> "Skipped index update. Run `/db-index` any time to regenerate it."

---

## Notes

- Do not write any file until the analyst explicitly types `save`
- If the analyst has loaded KB context from a prior `/db-explain` or `/db-orient` in this session, use it to pre-fill the Purpose and Gotchas sections before running introspection — but always refresh with live introspection data
- For Oracle: qualify the table as `{SCHEMA}.{TABLE}` in all introspection queries
- For Salesforce: use `describeSObject` via the `db-soql` skill instead of `db-introspect`; the output maps to the same KB entry format with object API name as the table name
- The `db-doc-writer` skill handles the draft format and gotcha detection — do not duplicate that logic here
