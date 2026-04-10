---
name: db-soql
description: This skill should be used whenever the active connection type is Salesforce (type: salesforce) and SOQL queries need to be constructed, relationship traversal is required, describeSObject metadata must be extracted, or API governor limits need to be checked. SOQL is not SQL — apply this skill to avoid syntax errors and silent data issues.
user-invocable: false
---

# DB SOQL Skill

Use this skill when the active connection type is `salesforce`. SOQL (Salesforce Object Query
Language) is structurally similar to SQL but with significant differences in join syntax,
metadata access, aggregate support, and hard governor limits that will error — not warn — if exceeded.

Always use this skill alongside `db-safety` and `db-cost-gate` — the cost gate for Salesforce
uses record count as the cost signal.

---

## SOQL vs SQL: Key Differences

| Feature | SQL | SOQL |
|---------|-----|------|
| Joins | `JOIN` keyword | Relationship queries (dot notation / subqueries) |
| Metadata | `INFORMATION_SCHEMA` | `describeSObject` via REST/Tooling API |
| Subqueries | Any position | Child-object subqueries in SELECT only |
| Soft delete | Varies by table | `IsDeleted = true` standard on all objects |
| Row limit | `LIMIT N` | `LIMIT N` (max 2,000 per synchronous query) |
| SELECT * | Supported | **Not supported** — must list fields explicitly |
| Wildcards | `LIKE '%x%'` | `LIKE '%x%'` supported; also `= 'value'` for picklists |
| Case sensitivity | Varies by DB | Field API names are case-insensitive; string comparisons are case-insensitive |
| NULL | `IS NULL` | `= null` or `!= null` (both forms work) |
| Transactions | Yes | No — SOQL is always read-only |
| Cross-object joins | Yes (arbitrary) | Only along defined relationship paths |

---

## Step 1: Get Field and Relationship Metadata via describeSObject

Before writing SOQL, always check the object's metadata when working with an unfamiliar object.
`describeSObject` is the Salesforce equivalent of `PRAGMA table_info` or `INFORMATION_SCHEMA.COLUMNS`.

### How to call describeSObject

Via the Salesforce REST API (use the connection credentials from `active.yaml`):

```
GET {instance_url}/services/data/v{api_version}/sobjects/{ObjectName}/describe/
```

Example:
```
GET https://myorg.my.salesforce.com/services/data/v59.0/sobjects/Account/describe/
```

### Key fields in the describe response

| Field | Meaning |
|-------|---------|
| `name` | API name of the field (use this in SOQL) |
| `label` | Human-readable label (displayed in UI) |
| `type` | Data type: string, double, boolean, date, datetime, id, reference, picklist, multipicklist, textarea, currency, percent, etc. |
| `referenceTo` | If type = reference: the object(s) this field points to |
| `relationshipName` | The name to use in relationship queries (dot notation or subquery FROM) |
| `nillable` | Whether the field can be null |
| `picklistValues` | For picklist fields: array of `{value, label, active, defaultValue}` |
| `filterable` | Whether this field can be used in a WHERE clause |
| `sortable` | Whether ORDER BY is supported on this field |
| `unique` | Whether the field is unique (useful for identifying natural keys) |

### Listing all objects in an org

```
GET {instance_url}/services/data/v{api_version}/sobjects/
```

Returns a list of all accessible sObjects with `name`, `label`, `queryable`, `createable`, etc.
Filter to `queryable: true` for objects you can run SOQL against.

---

## Step 2: SOQL Syntax Reference

### Basic query

```soql
SELECT Id, Name, CreatedDate, OwnerId
FROM Account
WHERE IsDeleted = false
LIMIT 200
```

**Required:** Always list fields explicitly — `SELECT *` is not valid SOQL.
**Default soft-delete filter:** `WHERE IsDeleted = false` — include unless querying deleted records.

### Filtering

```soql
-- Equality
WHERE Status = 'Active'

-- Comparison
WHERE Amount > 10000

-- IN list
WHERE StageName IN ('Prospecting', 'Qualification', 'Proposal')

-- NULL checks
WHERE CloseDate = null
WHERE AccountId != null

-- Date literals (preferred over hardcoded dates)
WHERE CloseDate = THIS_QUARTER
WHERE CreatedDate >= LAST_N_DAYS:30
WHERE LastModifiedDate > 2024-01-01T00:00:00Z

-- LIKE (case-insensitive, % wildcard, _ single char)
WHERE Name LIKE 'Acme%'

-- NOT IN
WHERE RecordTypeId NOT IN ('012000000000001', '012000000000002')
```

### Ordering and limiting

```soql
SELECT Id, Name, Amount
FROM Opportunity
WHERE IsDeleted = false
ORDER BY Amount DESC NULLS LAST
LIMIT 100
OFFSET 200    -- pagination; max offset is 2,000
```

---

## Step 3: Relationship Queries (Joins)

Salesforce does not use `JOIN`. Instead, relationships between objects are traversed using:
1. **Child-to-parent** — dot notation in SELECT (equivalent to inner join to parent)
2. **Parent-to-child** — subquery in SELECT (equivalent to left join to children)

### Child-to-parent (dot notation)

Navigate from child to parent using the **relationship name** (not the field name):

```soql
-- Contact.AccountId is the FK field
-- Contact.Account is the relationship name → gives access to parent Account fields
SELECT
  Id,
  FirstName,
  LastName,
  Account.Name,
  Account.Industry,
  Account.BillingCity
FROM Contact
WHERE IsDeleted = false
LIMIT 200
```

Rules:
- Relationship name comes from the `relationshipName` property in describeSObject for the lookup field
- Standard relationships: `Account.Name`, `Owner.Name`, `CreatedBy.Email`, `RecordType.Name`
- You can traverse up to **5 levels** of parent relationships
- Cannot traverse across unrelated objects

### Parent-to-child (subquery)

Query parent records and include related child records in a nested list:

```soql
SELECT
  Id,
  Name,
  (SELECT Id, FirstName, LastName, Email
   FROM Contacts
   WHERE IsDeleted = false)
FROM Account
WHERE IsDeleted = false
LIMIT 50
```

Rules:
- Subquery is in the SELECT clause only — not WHERE or FROM at the outer level
- Subquery FROM uses the **child relationship name** (plural form, from parent's describe response)
- Standard child relationships: `Contacts`, `Opportunities`, `Cases`, `Activities`
- Custom object child relationship names typically end in `__r` (e.g., `Custom_Objects__r`)
- Subquery can have its own WHERE, ORDER BY, LIMIT
- Up to **1 level** of child subquery

### Finding relationship names

```
-- From describeSObject on the PARENT object:
-- Response.childRelationships[].relationshipName → use in parent-to-child subquery FROM

-- From describeSObject on the CHILD object:
-- Response.fields[where type='reference'].relationshipName → use in child-to-parent dot notation
```

---

## Step 4: Aggregate Queries

```soql
-- COUNT all records
SELECT COUNT()
FROM Account
WHERE IsDeleted = false

-- COUNT with field (counts non-null values)
SELECT COUNT(Id)
FROM Account
WHERE IsDeleted = false

-- GROUP BY with aggregates
SELECT StageName, COUNT(Id) totalCount, SUM(Amount) totalAmount
FROM Opportunity
WHERE IsDeleted = false AND IsClosed = false
GROUP BY StageName
ORDER BY COUNT(Id) DESC

-- HAVING (filter on aggregate result)
SELECT AccountId, COUNT(Id) oppCount
FROM Opportunity
WHERE IsDeleted = false
GROUP BY AccountId
HAVING COUNT(Id) > 5
```

Supported aggregate functions: `COUNT()`, `COUNT(field)`, `SUM()`, `AVG()`, `MIN()`, `MAX()`

Limitations:
- Cannot use relationship dot notation in GROUP BY
- Cannot mix aggregated and non-aggregated fields without GROUP BY
- `COUNT()` (no field) returns a single integer, not grouped results

---

## Step 5: Picklist Values

Filtering on a picklist field with a value that doesn't match a defined picklist option returns
0 rows silently — no error. Always verify picklist values before filtering.

```
-- From describeSObject response:
-- fields[where name='Status'].picklistValues → array of {value, label, active}
-- Use the 'value' (API value), not the 'label', in SOQL WHERE clauses
```

Example — do not guess status values:
```soql
-- Wrong (if picklist value is 'New' not 'new' -- though SOQL is case-insensitive for strings,
-- wrong value entirely returns 0 rows)
WHERE Status = 'Open'

-- Right (after checking describe)
WHERE Status = 'New'
```

---

## Step 6: Governor Limits and Safety

Salesforce enforces hard limits that result in errors, not warnings. Know these before querying.

| Limit | Synchronous (per transaction) | Async / Bulk API |
|-------|-------------------------------|-----------------|
| SOQL query rows returned | 50,000 | 250,000,000 (Bulk) |
| SOQL queries per transaction | 100 | N/A |
| Heap size | 6 MB | 12 MB |

### Before running a large query

Always estimate the row count first:

```soql
SELECT COUNT()
FROM {Object}
WHERE {same filters as your query}
```

If `COUNT()` > 40,000, warn the analyst and suggest:
1. Add more WHERE filters to narrow scope
2. Use LIMIT with OFFSET pagination for data export
3. Use Bulk API or Data Loader for full object exports (out of scope for Claude Code)

### Non-selective filter warning

SOQL queries on large objects (> 200,000 records) with non-selective WHERE clauses will time out
or throw `QUERY_TIMEOUT` / `System.LimitException` errors.

A filter is **selective** if it uses one of:
- `Id` field
- An indexed field (fields marked `externalId: true` in describe, or standard indexed fields: `Name`, `CreatedDate`, `OwnerId`, `RecordTypeId`)
- A unique field
- A field with a selective index (check with SF admin)

Non-selective filters on large objects:
```soql
-- Risky on large Account objects (non-indexed custom field):
WHERE My_Custom_Field__c = 'value'

-- Safe (indexed field):
WHERE CreatedDate >= LAST_N_DAYS:7
WHERE OwnerId = '0050000000xxxx'
```

If the target object has > 100,000 records and the WHERE clause does not use an indexed field,
flag this before executing:
> ⚠️ `{Object}` has approximately {N} records. The filter `{field}` may not be indexed, which risks a query timeout. Consider adding `Id IN (...)` or a date range filter on `CreatedDate` to make the query selective.

---

## Step 7: Safe Object Exploration Pattern

When working with an unfamiliar Salesforce object, follow this sequence:

```
1. Call describeSObject to get field list, types, and relationship names
2. Note which fields are filterable, which are picklists, and their valid values
3. Estimate row count: SELECT COUNT() FROM {Object} WHERE IsDeleted = false
4. Sample safely: SELECT {key_fields} FROM {Object} WHERE IsDeleted = false LIMIT 10
5. For relationships: check childRelationships and lookup field relationshipNames
```

---

## Common Standard Objects Reference

| Object | What it represents | Common soft-delete pattern |
|--------|-------------------|---------------------------|
| `Account` | Companies or individuals | `IsDeleted = false` |
| `Contact` | People associated with accounts | `IsDeleted = false` |
| `Opportunity` | Sales deals | `IsDeleted = false`; also `IsClosed`, `IsWon` |
| `Lead` | Unqualified prospects | `IsDeleted = false`; `IsConverted` |
| `Case` | Support tickets | `IsDeleted = false`; `Status != 'Closed'` |
| `Task` / `Event` | Activities | `IsDeleted = false`; `ActivityDate` |
| `User` | Salesforce users | `IsActive = true` to filter deactivated users |
| `RecordType` | Record type definitions | Used via `RecordType.Name` relationship |
| `Profile` | User permission profiles | — |

Custom objects end in `__c` (e.g., `Invoice__c`). Custom fields end in `__c`.
Custom relationship names end in `__r` (e.g., `Invoice__r` as a relationship name).

---

## Notes

- `describeSObject` is the primary metadata source — treat it like `PRAGMA table_info` or `ALL_COLUMNS`
- Always check picklist values before filtering — wrong values fail silently
- Never use `SELECT *` — it is a syntax error in SOQL
- `IsDeleted = false` is the universal soft-delete filter — always include unless explicitly querying deleted records (recycle bin)
- The `db-safety` hook blocks REST API write calls (POST/PATCH/DELETE) — SOQL SELECT queries are read-only by design
- For the `db-explain-result` skill: note that Salesforce result sets are limited to 2,000 rows per API call by default (paged); a result showing exactly 2,000 rows may be truncated
