# TABLE_NAME

**Schema:** `example_schema`
**Database:** Snowflake / Oracle / Athena / SQLite / Salesforce
**Last Documented:** YYYY-MM-DD
**Documented By:** [analyst name or "introspection"]
**Rows (approx):** unknown

## Purpose

Plain-English description of what this table represents. What real-world thing does
each row capture? What system or process produces this data? Who or what consumes it?

## Grain

One row per [entity — e.g., "order", "customer", "daily snapshot per product"].

## Key Fields

| Column | Type | Nullable | Description | Notes |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | No | Primary key | Primary key |
| `status` | VARCHAR | No | Record status | Status — see Gotchas |
| `created_at` | TIMESTAMP | No | When the record was created | UTC |
| `updated_at` | TIMESTAMP | Yes | When the record was last modified | UTC; NULL if never updated |

## Relationships

- `this_table.foreign_key_col` → `other_schema.other_table.id` (many-to-one)
- ← `child_table.parent_id` references this table (one-to-many)

*No FK relationships detected from introspection. Add manually if known.*

## Gotchas

- ⚠️ **Soft delete** — `deleted_at` is NOT NULL for deleted rows. Always filter
  `WHERE deleted_at IS NULL` before joining or aggregating.
- ⚠️ **Status codes** — `status` uses coded values: 'A' (active), 'I' (inactive), 'D' (deleted).
  Confirm additional values with the data owner.
- ⚠️ **Timezone** — `created_at` and `updated_at` are stored in UTC. Convert for local reporting.

*No gotchas identified from introspection. Add entries as you discover them.*

## Sample Query

```sql
-- Active records with key fields
-- Returns: one row per [grain]
SELECT
  id,
  status,
  created_at
FROM example_schema.TABLE_NAME
WHERE deleted_at IS NULL
LIMIT 20;
```

## Open Questions

- [ ] What does status = 'P' mean? Needs confirmation from data owner.
- [ ] Is `updated_at` reliably populated for all rows, or only recent data?
