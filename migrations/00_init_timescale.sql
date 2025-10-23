-- Enable timescaledb extension (requires superuser on PG cluster)
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Recommended PG extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
