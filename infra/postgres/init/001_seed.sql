BEGIN;
CREATE SCHEMA IF NOT EXISTS sales_raw;
CREATE TABLE IF NOT EXISTS sales_raw.customers (
    customer_id integer PRIMARY KEY,
    customer_name text NOT NULL
);
CREATE TABLE IF NOT EXISTS sales_raw.orders (
    order_id integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES sales_raw.customers,
    amount_text text NOT NULL,
    ordered_at_text text NOT NULL,
    status text NOT NULL
);
INSERT INTO sales_raw.customers
SELECT i, 'Demo customer ' || i FROM generate_series(1, 10) i
ON CONFLICT (customer_id) DO UPDATE SET customer_name = excluded.customer_name;
INSERT INTO sales_raw.orders
SELECT i, ((i - 1) % 10) + 1, (i * 1000)::text,
       (DATE '2026-01-01' + (i - 1))::text, 'paid'
FROM generate_series(1, 30) i
ON CONFLICT (order_id) DO UPDATE SET
    customer_id = excluded.customer_id, amount_text = excluded.amount_text,
    ordered_at_text = excluded.ordered_at_text, status = excluded.status;
COMMIT;
-- Golden result: 30 orders, 10 customers, total amount 465000.00.
