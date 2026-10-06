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
-- Intentionally dirty (Sprint 2): same true values, inconsistent formatting. Correct cleaning still gives the golden result.
--   dates: order_id divisible by 3 -> DD/MM/YYYY, by 5 -> YYYY/MM/DD, otherwise YYYY-MM-DD;
--   amounts: divisible by 4 -> padded with spaces, by 7 -> trailing ".00".
INSERT INTO sales_raw.orders
SELECT i, ((i - 1) % 10) + 1,
       CASE WHEN i % 4 = 0 THEN ' ' || (i * 1000) || ' '
            WHEN i % 7 = 0 THEN (i * 1000) || '.00'
            ELSE (i * 1000)::text END,
       CASE WHEN i % 3 = 0 THEN to_char(DATE '2026-01-01' + (i - 1), 'DD/MM/YYYY')
            WHEN i % 5 = 0 THEN to_char(DATE '2026-01-01' + (i - 1), 'YYYY/MM/DD')
            ELSE (DATE '2026-01-01' + (i - 1))::text END,
       'paid'
FROM generate_series(1, 30) i
ON CONFLICT (order_id) DO UPDATE SET
    customer_id = excluded.customer_id, amount_text = excluded.amount_text,
    ordered_at_text = excluded.ordered_at_text, status = excluded.status;
COMMIT;
-- Golden result (all three date formats parsed): 30 orders, 10 customers, total amount 465000.00.
-- A plain TRY_CAST AS date (manual cleaning screen) rejects the 10 DD/MM/YYYY rows: 20 orders, 300000.00.
