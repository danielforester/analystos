# Demo Database

A self-contained SQLite database for testing the DB Analyst Framework without
any cloud credentials. It simulates a small B2B SaaS company with sales and HR data.

---

## Quick Start

```bash
# 1. Create the database
sqlite3 demo/demo.db < demo/create-demo-db.sql

# 2. Verify it worked
sqlite3 demo/demo.db "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
# Expected output:
# hr_departments
# hr_employees
# sales_customers
# sales_order_items
# sales_orders
# sales_products

# 3. Copy the demo connection config to your project
cp demo/demo-connections.yaml .claude/db-connections/active.yaml
```

Then start a Claude Code session — the framework will auto-load the connection and KB.

---

## Schema Overview

Two simulated schemas (distinguished by table name prefix):

### `hr_` — Human Resources
| Table | Description |
|---|---|
| `hr_departments` | 5 departments with budget and location |
| `hr_employees` | 10 employees with org hierarchy, salary, region |

### `sales_` — Sales & Orders
| Table | Description |
|---|---|
| `sales_customers` | 7 customers across segments; 1 soft-deleted |
| `sales_products` | 7 products; 1 discontinued |
| `sales_orders` | 10 orders in various statuses (open, shipped, cancelled) |
| `sales_order_items` | 16 line items; prices captured at time of order |

### Cross-schema join
`sales_orders.employee_id` → `hr_employees.employee_id`
(the rep who closed the deal)

---

## Built-In Gotchas

These are intentional data quality issues, documented in `demo-knowledge/_gotchas.md`:

| Gotcha | Table | Detail |
|---|---|---|
| Soft deletes | `sales_customers` | Use `WHERE deleted_at IS NULL` |
| Discontinued products | `sales_products` | Appear in old `sales_order_items` |
| Snapshot prices | `sales_order_items` | `unit_price` ≠ current `sales_products.unit_price` |
| Active flag (not deleted_at) | `hr_employees` | `is_active = 1` for current staff |
| NULL email | `hr_employees` | employee_id=10 has no email |
| Terminated employee's orders | `sales_orders` | employee_id=9 is inactive but has orders |

---

## Sample Queries

```sql
-- Active customers (excluding churned)
SELECT * FROM sales_customers WHERE deleted_at IS NULL;

-- Revenue by customer (shipped orders only, excluding discounts)
SELECT
    c.company_name,
    SUM(i.quantity * i.unit_price * (1 - i.discount)) AS net_revenue
FROM sales_orders o
JOIN sales_customers c ON o.customer_id = c.customer_id
JOIN sales_order_items i ON o.order_id = i.order_id
WHERE o.status = 'shipped'
  AND c.deleted_at IS NULL
GROUP BY c.company_name
ORDER BY net_revenue DESC;

-- Employee + their manager
SELECT
    e.first_name || ' ' || e.last_name AS employee,
    m.first_name || ' ' || m.last_name AS manager,
    e.job_title,
    d.department_name
FROM hr_employees e
LEFT JOIN hr_employees m ON e.manager_id = m.employee_id
JOIN hr_departments d ON e.department_id = d.department_id
WHERE e.is_active = 1;
```

---

## Demo Knowledge Base

A pre-populated knowledge base for the demo database lives in `demo/demo-knowledge/`.
To use it, copy it into your project:

```bash
cp -r demo/demo-knowledge/ db-knowledge/
```
