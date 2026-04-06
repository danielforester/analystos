# Cross-Schema Gotchas

Known issues, traps, and non-obvious behaviors that apply across multiple schemas or
to the database as a whole. Always check here before writing queries or drawing conclusions.

**Last updated:** {YYYY-MM-DD}

---

## Active Gotchas

### ⚠️ [EXAMPLE] Deleted records are soft-deleted, not removed
**Affects:** All tables with a `deleted_at` or `is_deleted` column
**Impact:** Row counts and aggregates will be inflated if you don't filter
**Fix:** Always add `WHERE deleted_at IS NULL` (or `WHERE is_deleted = 0`) to queries
**Discovered:** {YYYY-MM-DD} by {analyst}

---

*(Add new gotchas above this line. Remove the example above once you have real entries.)*

---

## Resolved Gotchas

*(Move gotchas here once they are fixed in the source system, with a resolution note.)*

---

## Gotcha Template

Copy this block for each new gotcha:

```
### ⚠️ [SHORT TITLE]
**Affects:** {tables or schemas affected}
**Impact:** {what goes wrong if you don't know about this}
**Fix:** {how to work around it}
**Discovered:** {YYYY-MM-DD} by {analyst}
**Ticket/Reference:** {optional link}
```
