# Cross-Schema Gotchas — Demo Database

**Last updated:** 2026-04-06

---

## Active Gotchas

### ⚠️ sales_customers uses soft deletes
**Affects:** `sales_customers`
**Impact:** Churned customers are NOT removed from the table. Row counts and customer
aggregates will include churned customers unless you filter.
**Fix:** Always add `WHERE deleted_at IS NULL` when querying active customers.
**Example:**
```sql
-- Wrong: includes churned Vandelay Industries
SELECT COUNT(*) FROM sales_customers;

-- Correct
SELECT COUNT(*) FROM sales_customers WHERE deleted_at IS NULL;
```
**Discovered:** 2026-04-06 by demo setup

---

### ⚠️ hr_employees uses is_active flag, NOT deleted_at
**Affects:** `hr_employees`
**Impact:** Terminated employees remain in the table. If you join employees to orders
without filtering, you'll get results attributed to people who no longer work here.
**Fix:** Add `WHERE is_active = 1` for current employees. Note this is **different** from
`sales_customers` which uses `deleted_at` — inconsistent soft-delete patterns across schemas.
**Example:**
```sql
-- Correct for active employees
SELECT * FROM hr_employees WHERE is_active = 1;
```
**Discovered:** 2026-04-06 by demo setup

---

### ⚠️ sales_order_items.unit_price is a snapshot, not current price
**Affects:** `sales_order_items` joined to `sales_products`
**Impact:** `sales_order_items.unit_price` is the price **at the time of the order**.
It will differ from `sales_products.unit_price` (the current list price).
**Fix:** Use `sales_order_items.unit_price` for revenue calculations. Use
`sales_products.unit_price` only for current pricing/catalog queries.
**Discovered:** 2026-04-06 by demo setup

---

### ⚠️ Discontinued products appear in historical orders
**Affects:** `sales_products` joined to `sales_order_items`
**Impact:** `sales_products.is_discontinued = 1` means the product is no longer sold,
but it still appears in old `sales_order_items` rows. Filtering out discontinued
products will incorrectly exclude historical revenue.
**Fix:** Only filter `WHERE is_discontinued = 0` when querying the **current catalog**.
Never filter on `is_discontinued` when calculating historical revenue.
**Discovered:** 2026-04-06 by demo setup

---

### ⚠️ One employee has a NULL email
**Affects:** `hr_employees`
**Impact:** `employee_id = 10` (James Wilson) has no email on file. Queries that
assume email is NOT NULL will silently exclude him.
**Fix:** Use `COUNT(email)` vs `COUNT(*)` to detect the difference; use `COALESCE(email, 'no-email')`
in output if needed.
**Discovered:** 2026-04-06 by demo setup
