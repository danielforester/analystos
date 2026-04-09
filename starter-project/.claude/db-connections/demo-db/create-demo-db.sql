-- DB Analyst Framework — Demo Database
-- SQLite schema with seed data for testing the framework without cloud credentials.
--
-- Two simulated "schemas" (via table name prefixes): sales_ and hr_
-- Intentional gotchas are documented in demo/demo-knowledge/_gotchas.md
--
-- Usage:
--   sqlite3 demo/demo.db < demo/create-demo-db.sql
--   # or from Python: sqlite3.connect("demo/demo.db"); cursor.executescript(...)

PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────────────────────
-- HR schema (prefix: hr_)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS hr_departments (
    department_id   INTEGER PRIMARY KEY,
    department_name TEXT    NOT NULL,
    location        TEXT,
    budget          REAL,
    created_at      TEXT    NOT NULL DEFAULT (date('now'))
);

CREATE TABLE IF NOT EXISTS hr_employees (
    employee_id     INTEGER PRIMARY KEY,
    first_name      TEXT    NOT NULL,
    last_name       TEXT    NOT NULL,
    email           TEXT    UNIQUE,
    hire_date       TEXT    NOT NULL,               -- YYYY-MM-DD
    job_title       TEXT,
    department_id   INTEGER REFERENCES hr_departments(department_id),
    manager_id      INTEGER REFERENCES hr_employees(employee_id),
    salary          REAL,
    is_active       INTEGER NOT NULL DEFAULT 1,     -- ⚠️ GOTCHA: 1=active, 0=terminated; no deleted_at
    region          TEXT                            -- NULL for HQ staff
);

CREATE INDEX IF NOT EXISTS idx_employees_dept ON hr_employees(department_id);
CREATE INDEX IF NOT EXISTS idx_employees_manager ON hr_employees(manager_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Sales schema (prefix: sales_)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sales_customers (
    customer_id     INTEGER PRIMARY KEY,
    company_name    TEXT    NOT NULL,
    contact_name    TEXT,
    country         TEXT    NOT NULL DEFAULT 'US',
    segment         TEXT,                           -- 'enterprise', 'mid-market', 'smb'
    created_at      TEXT    NOT NULL DEFAULT (date('now')),
    deleted_at      TEXT                            -- ⚠️ GOTCHA: soft delete — filter WHERE deleted_at IS NULL
);

CREATE TABLE IF NOT EXISTS sales_products (
    product_id      INTEGER PRIMARY KEY,
    product_name    TEXT    NOT NULL,
    category        TEXT,
    unit_price      REAL    NOT NULL,
    is_discontinued INTEGER NOT NULL DEFAULT 0      -- ⚠️ GOTCHA: discontinued products still appear in old orders
);

CREATE TABLE IF NOT EXISTS sales_orders (
    order_id        INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES sales_customers(customer_id),
    employee_id     INTEGER REFERENCES hr_employees(employee_id),   -- the rep who closed it
    order_date      TEXT    NOT NULL,               -- YYYY-MM-DD
    ship_date       TEXT,                           -- NULL if not yet shipped
    status          TEXT    NOT NULL DEFAULT 'open',-- 'open', 'shipped', 'cancelled', 'refunded'
    region          TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_customer ON sales_orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_employee ON sales_orders(employee_id);
CREATE INDEX IF NOT EXISTS idx_orders_date ON sales_orders(order_date);

CREATE TABLE IF NOT EXISTS sales_order_items (
    item_id         INTEGER PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES sales_orders(order_id),
    product_id      INTEGER NOT NULL REFERENCES sales_products(product_id),
    quantity        INTEGER NOT NULL DEFAULT 1,
    unit_price      REAL    NOT NULL,               -- ⚠️ GOTCHA: price at time of order, may differ from current sales_products.unit_price
    discount        REAL    NOT NULL DEFAULT 0.0    -- decimal fraction: 0.1 = 10% off
);

CREATE INDEX IF NOT EXISTS idx_items_order ON sales_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_items_product ON sales_order_items(product_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Seed data — HR
-- ─────────────────────────────────────────────────────────────────────────────

INSERT INTO hr_departments VALUES
  (1, 'Engineering',  'San Francisco', 2500000.00, '2018-01-15'),
  (2, 'Sales',        'New York',      1800000.00, '2018-01-15'),
  (3, 'Marketing',    'New York',      900000.00,  '2018-03-01'),
  (4, 'HR & People',  'San Francisco', 600000.00,  '2018-01-15'),
  (5, 'Finance',      'Chicago',       750000.00,  '2019-06-01');

INSERT INTO hr_employees VALUES
  (1,  'Alice',   'Chen',      'alice@example.com',  '2018-02-01', 'VP Engineering',   1, NULL, 210000.00, 1, NULL),
  (2,  'Bob',     'Martinez',  'bob@example.com',    '2018-03-15', 'VP Sales',         2, NULL, 195000.00, 1, NULL),
  (3,  'Carol',   'Johnson',   'carol@example.com',  '2018-04-01', 'Sr. Engineer',     1, 1,    145000.00, 1, 'West'),
  (4,  'David',   'Kim',       'david@example.com',  '2019-01-10', 'Engineer',         1, 1,    125000.00, 1, 'West'),
  (5,  'Eva',     'Patel',     'eva@example.com',    '2019-06-01', 'Account Exec',     2, 2,    95000.00,  1, 'East'),
  (6,  'Frank',   'Williams',  'frank@example.com',  '2020-02-15', 'Account Exec',     2, 2,    88000.00,  1, 'Central'),
  (7,  'Grace',   'Lee',       'grace@example.com',  '2020-08-01', 'Marketing Mgr',    3, NULL, 110000.00, 1, NULL),
  (8,  'Henry',   'Brown',     'henry@example.com',  '2021-01-05', 'Engineer',         1, 3,    118000.00, 1, 'East'),
  (9,  'Isabel',  'Davis',     'isabel@example.com', '2021-05-17', 'Account Exec',     2, 2,    82000.00,  0, 'West'),  -- terminated
  (10, 'James',   'Wilson',    NULL,                 '2022-03-01', 'HR Specialist',    4, NULL, 78000.00,  1, NULL);   -- no email on file

-- ─────────────────────────────────────────────────────────────────────────────
-- Seed data — Sales
-- ─────────────────────────────────────────────────────────────────────────────

INSERT INTO sales_customers VALUES
  (1, 'Acme Corp',         'Wile E. Coyote', 'US', 'enterprise',  '2019-03-01', NULL),
  (2, 'Globex Corp',       'Hank Scorpio',   'US', 'enterprise',  '2019-07-15', NULL),
  (3, 'Initech',           'Bill Lumbergh',  'US', 'mid-market',  '2020-01-10', NULL),
  (4, 'Umbrella Ltd',      'Albert Wesker',  'UK', 'enterprise',  '2020-06-01', NULL),
  (5, 'Pied Piper',        'Richard Hendricks','US','smb',        '2021-02-28', NULL),
  (6, 'Vandelay Industries','Art Vandelay',   'US', 'smb',        '2021-09-01', '2023-01-15'),  -- churned/deleted
  (7, 'Dunder Mifflin',    'Michael Scott',  'US', 'mid-market',  '2022-01-03', NULL);

INSERT INTO sales_products VALUES
  (1, 'Analyst Pro License',    'Software',  1200.00, 0),
  (2, 'Team Bundle (5 seats)',  'Software',  4500.00, 0),
  (3, 'Enterprise License',     'Software', 12000.00, 0),
  (4, 'Implementation Services','Services',  8000.00, 0),
  (5, 'Training Workshop',      'Services',  2500.00, 0),
  (6, 'Legacy Data Export',     'Services',  3000.00, 1),   -- discontinued
  (7, 'Annual Support',         'Support',   1500.00, 0);

INSERT INTO sales_orders VALUES
  (1001, 1, 5, '2022-03-15', '2022-03-20', 'shipped',   'East',    NULL),
  (1002, 2, 5, '2022-04-01', '2022-04-05', 'shipped',   'East',    NULL),
  (1003, 3, 6, '2022-05-10', NULL,         'open',      'Central', 'Waiting on PO'),
  (1004, 4, 5, '2022-06-20', '2022-06-28', 'shipped',   'East',    NULL),
  (1005, 1, 5, '2022-09-01', '2022-09-10', 'shipped',   'East',    'Renewal'),
  (1006, 5, 6, '2022-10-15', NULL,         'cancelled', 'Central', 'Customer backed out'),
  (1007, 6, 6, '2022-11-01', '2022-11-08', 'shipped',   'Central', NULL),   -- churned customer
  (1008, 7, 5, '2023-01-20', '2023-01-25', 'shipped',   'East',    NULL),
  (1009, 2, 5, '2023-03-05', '2023-03-12', 'shipped',   'East',    'Expansion'),
  (1010, 3, 6, '2023-04-01', NULL,         'open',      'Central', NULL);

INSERT INTO sales_order_items VALUES
  (1, 1001, 3, 1, 12000.00, 0.0),
  (2, 1001, 4, 1,  8000.00, 0.0),
  (3, 1002, 3, 1, 12000.00, 0.10),  -- 10% discount
  (4, 1002, 7, 1,  1500.00, 0.0),
  (5, 1003, 2, 2,  4500.00, 0.05),
  (6, 1004, 3, 1, 12000.00, 0.15),  -- 15% discount (enterprise deal)
  (7, 1004, 4, 1,  8000.00, 0.0),
  (8, 1004, 5, 2,  2500.00, 0.0),
  (9, 1005, 3, 1, 12000.00, 0.0),
  (10,1005, 7, 1,  1500.00, 0.0),
  (11,1006, 1, 3,  1200.00, 0.0),   -- cancelled order
  (12,1007, 6, 1,  3000.00, 0.0),   -- discontinued product + churned customer
  (13,1008, 2, 1,  4500.00, 0.0),
  (14,1009, 3, 2, 12000.00, 0.20),  -- 20% expansion discount
  (15,1009, 7, 2,  1500.00, 0.0),
  (16,1010, 1, 5,  1200.00, 0.0);
