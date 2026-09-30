#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv reader_password PIPELINE_READER_PASSWORD
SELECT 'CREATE ROLE pipeline_reader LOGIN' WHERE NOT EXISTS
    (SELECT FROM pg_roles WHERE rolname = 'pipeline_reader') \gexec
ALTER ROLE pipeline_reader WITH PASSWORD :'reader_password';
ALTER ROLE pipeline_reader SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE pipeline_demo TO pipeline_reader;
GRANT USAGE ON SCHEMA sales_raw TO pipeline_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sales_raw TO pipeline_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA sales_raw GRANT SELECT ON TABLES TO pipeline_reader;
SQL
