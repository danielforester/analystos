# {SCHEMA_NAME} — Schema Overview

**Database:** <!-- Oracle / Snowflake / Athena / Salesforce -->
**Schema / Dataset:** `{schema_name}`
**Last Updated:** YYYY-MM-DD
**Maintained By:** <!-- Analyst name(s) -->
**Status:** <!-- Draft / Reviewed / Trusted -->

---

## 1. Domain Summary

<!-- 2–4 sentences answering: What business domain does this schema serve?
     Who owns the data? What processes or systems produce it? What decisions is it used to support? -->

**Business Domain:** <!-- e.g., Order Management, Customer Identity, Finance, Sales CRM -->
**Source System(s):** <!-- e.g., Salesforce, SAP, internal microservice `orders-api` -->
**Primary Consumers:** <!-- e.g., Revenue Analytics team, Finance reporting, Data Science -->
**Refresh Cadence:** <!-- e.g., Daily batch at 02:00 UTC, Near-real-time via CDC, Manual -->

---

## 2. Entity Clusters

<!-- Group tables by the business concept they describe.
     This is the fastest way to orient a new analyst — they can ignore irrelevant clusters entirely.
     Each cluster gets a short label, a one-line description, and a list of its tables.
     A table can appear in more than one cluster if it bridges domains. -->

### Cluster A: {Cluster Name}
<!-- e.g., "Customer Identity", "Order Lifecycle", "Product Catalog" -->

> {One sentence describing what this cluster represents and why it exists.}

| Table | Role | Notes |
|-------|------|-------|
| `TABLE_A` | Core entity | Primary key is `customer_id` |
| `TABLE_B` | Attributes / detail | One row per customer per attribute type |
| `TABLE_C` | History / audit | Append-only; use `MAX(effective_date)` for current state |

---

### Cluster B: {Cluster Name}

> {One sentence.}

| Table | Role | Notes |
|-------|------|-------|
| `TABLE_D` | Core entity | |
| `TABLE_E` | Bridge / association | Resolves many-to-many between TABLE_A and TABLE_D |

---

### Cluster C: {Cluster Name}

> {One sentence.}

| Table | Role | Notes |
|-------|------|-------|
| `TABLE_F` | Reference / lookup | Low row count; safe to broadcast join |
| `TABLE_G` | Fact / event | High volume; always filter by `event_date` partition first |

---

<!-- Add clusters as needed. Aim for 3–7 clusters; if you have more, consider splitting the schema overview. -->

---

## 3. Key Relationships

<!-- This section is the primary reference for query-building.
     Each relationship is documented in a consistent format designed to answer the questions
     a query author needs answered before writing a JOIN:
       - What are the exact join keys?
       - What is the cardinality, and which side is "many"?
       - Is an INNER or LEFT JOIN correct here?
       - Are there filter prerequisites before joining?
       - What are the known gotchas that cause duplicates or dropped rows? -->

### Relationship Format Reference

Each relationship entry follows this structure:

```
## [LEFT_TABLE] → [RIGHT_TABLE]
Cardinality : [1:1 | 1:many | many:1 | many:many]
Join type   : [INNER | LEFT | Depends — see notes]
Left key    : left_table.column_name
Right key   : right_table.column_name
Direction   : [Bidirectional | Preferred: LEFT_TABLE → RIGHT_TABLE]
Pre-filters : Filters that MUST be applied before joining to avoid fan-out or wrong results
Gotchas     : Known issues, nulls, encoding quirks, historical rows, etc.
Example     : Minimal illustrative JOIN snippet
```

Cardinality is always written from **left → right**:
- `1:1` — each row on the left matches at most one row on the right
- `1:many` — each left row may match multiple right rows (RIGHT side is the "many")
- `many:1` — multiple left rows map to one right row (LEFT side is the "many")
- `many:many` — requires a bridge table; document the bridge separately

---

### {LEFT_TABLE} → {RIGHT_TABLE}

```
Cardinality : 1:many
Join type   : LEFT (right side may be absent for new/draft records)
Left key    : {left_table}.{left_key_column}
Right key   : {right_table}.{right_key_column}
Direction   : Preferred: {left_table} → {right_table}
Pre-filters : Filter {right_table} WHERE status != 'D' before joining (soft deletes)
Gotchas     : None known
```

```sql
-- Example
SELECT
    l.{left_key_column},
    r.{some_column}
FROM {left_table} l
LEFT JOIN {right_table} r
    ON l.{left_key_column} = r.{right_key_column}
WHERE r.status != 'D'  -- exclude soft-deleted right-side rows
```

---

### {TABLE_A} → {TABLE_B}

```
Cardinality : 1:1
Join type   : LEFT (TABLE_B row may not exist for all TABLE_A rows)
Left key    : table_a.record_id
Right key   : table_b.record_id
Direction   : Bidirectional
Pre-filters : None required
Gotchas     : table_b is only populated after the record reaches status = 'COMPLETE'.
              Joining before that point will produce NULLs on the right side — use LEFT JOIN.
```

```sql
-- Example
SELECT
    a.record_id,
    a.status,
    b.completion_date
FROM table_a a
LEFT JOIN table_b b
    ON a.record_id = b.record_id
```

---

### {TABLE_C} → {TABLE_D}  *(Fan-out risk)*

```
Cardinality : 1:many
Join type   : INNER or LEFT depending on intent — see gotchas
Left key    : table_c.entity_id
Right key   : table_d.entity_id
Direction   : Preferred: table_c → table_d
Pre-filters : MUST filter table_d WHERE is_current = TRUE before joining,
              otherwise each table_c row fans out to N historical rows.
Gotchas     : ⚠️  FAN-OUT RISK. table_d is a slowly-changing dimension (SCD Type 2).
              Without the is_current filter, row counts in aggregations will be inflated.
              Verify: SELECT entity_id, COUNT(*) FROM table_d GROUP BY 1 HAVING COUNT(*) > 1
```

```sql
-- Safe example — current state only
SELECT
    c.entity_id,
    d.dimension_value
FROM table_c c
INNER JOIN table_d d
    ON c.entity_id = d.entity_id
    AND d.is_current = TRUE   -- ⚠️ Required to prevent fan-out

-- Historical example — all states
SELECT
    c.entity_id,
    d.effective_from,
    d.effective_to,
    d.dimension_value
FROM table_c c
INNER JOIN table_d d
    ON c.entity_id = d.entity_id
-- No is_current filter here — intentional
```

---

### {TABLE_E} ↔ {TABLE_F}  *(Many-to-Many via bridge)*

```
Cardinality : many:many  (resolved via bridge table: {TABLE_BRIDGE})
Join type   : Use bridge table; never join TABLE_E directly to TABLE_F
Left key    : table_e.e_id → table_bridge.e_id
Right key   : table_f.f_id → table_bridge.f_id
Direction   : Bidirectional via bridge
Pre-filters : None on bridge; filter source tables before joining bridge
Gotchas     : Direct join of TABLE_E to TABLE_F without the bridge is unsupported
              and will produce a cartesian product.
```

```sql
-- Example
SELECT
    e.e_id,
    f.f_id,
    b.relationship_type
FROM table_e e
INNER JOIN table_bridge b ON e.e_id = b.e_id
INNER JOIN table_f f      ON b.f_id = f.f_id
WHERE b.is_active = TRUE
```

---

<!-- Add one entry per meaningful join path. Prioritize:
     1. High-frequency joins analysts use regularly
     2. Any join with a fan-out risk or non-obvious filter prerequisite
     3. Any join where the key name differs between tables (e.g., orders.id → line_items.order_id)
     Low-risk, obvious joins (e.g., lookup table on a code column) can be noted briefly in Section 4. -->

---

## 4. Relationship Map (Summary Table)

<!-- A compact reference for scanning all join paths at once.
     Pairs with Section 3; the detail lives there, this is the index. -->

| From Table | To Table | Via | Cardinality | Join Type | Pre-filter Required? | Risk |
|------------|----------|-----|-------------|-----------|----------------------|------|
| `table_a` | `table_b` | `table_a.id = table_b.a_id` | 1:many | LEFT | No | Low |
| `table_c` | `table_d` | `table_c.entity_id = table_d.entity_id` | 1:many | INNER/LEFT | ✅ `is_current = TRUE` | ⚠️ Fan-out |
| `table_e` | `table_f` | via `table_bridge` | many:many | INNER (bridge) | No | Low |
| `table_a` | `table_f` | `table_a.type_code = table_f.code` | many:1 | LEFT | No | Low |

---

## 5. Common Query Patterns

<!-- 2–5 starter queries that cover the most frequent analyst use cases for this schema.
     These are not exhaustive — full examples live in _queries/.
     Goal: give an analyst a working foundation in under 2 minutes. -->

### Pattern 1: {Descriptive Name, e.g., "Current state of all active entities"}

```sql
-- Purpose: {What question does this answer?}
-- Returns: {One row per what? What's the grain of the result?}
-- Notes:   {Any caveats about the result set}

SELECT
    a.id,
    a.status,
    b.detail_value
FROM {schema}.table_a a
LEFT JOIN {schema}.table_b b
    ON a.id = b.a_id
WHERE a.status != 'D'  -- exclude soft-deleted
```

---

### Pattern 2: {Descriptive Name}

```sql
-- Purpose:
-- Returns:
-- Notes:

SELECT
    ...
FROM {schema}.table_c c
INNER JOIN {schema}.table_d d
    ON c.entity_id = d.entity_id
    AND d.is_current = TRUE
```

---

## 6. Schema-Level Gotchas

<!-- Issues that apply broadly to this schema — not specific to one table.
     Table-specific gotchas live in the individual {table}.md files. -->

- <!-- e.g., "All timestamps in this schema are stored in UTC. Convert to America/New_York for business reporting." -->
- <!-- e.g., "Soft deletes are universal in this schema: always filter WHERE status != 'D' or WHERE is_deleted = 0." -->
- <!-- e.g., "Row counts in TABLE_X are unreliable before 2022-01-01 due to a migration gap." -->

---

## 7. Open Questions

<!-- Unresolved questions about this schema. Move to _open-questions.md if cross-schema. -->

- [ ] <!-- e.g., "Confirm the meaning of status = 'P' with the data owner (Sarah, Ops team)." -->
- [ ] <!-- e.g., "TABLE_G has a join key `legacy_id` that doesn't appear in TABLE_A — is this from the pre-2021 system?" -->

---

## 8. Change Log

<!-- Brief notes on significant updates to this document. -->

| Date | Author | Change |
|------|--------|--------|
| YYYY-MM-DD | {Name} | Initial draft from introspection |
