-- Tài khoản pipeline_reader (002_reader.sh) chỉ được đọc schema pagila (003_pagila.sql.gz).
GRANT USAGE ON SCHEMA pagila TO pipeline_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA pagila TO pipeline_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA pagila GRANT SELECT ON TABLES TO pipeline_reader;
