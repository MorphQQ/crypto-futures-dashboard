"""
TimescaleDB / PostgreSQL schema initializer for crypto-futures-dashboard.
Fixes primary key issues and safely enables hypertables.
"""

import asyncio
import asyncpg
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/futures")

CREATE_TABLES_SQL = """
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS metrics (
    id BIGSERIAL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    price DOUBLE PRECISION,
    price_change_24h_pct DOUBLE PRECISION,
    volume_24h DOUBLE PRECISION,
    volume_change_24h_pct DOUBLE PRECISION,
    market_cap DOUBLE PRECISION,
    oi_usd DOUBLE PRECISION,
    oi_abs_usd DOUBLE PRECISION,
    oi_change_24h_pct DOUBLE PRECISION,
    oi_change_5m_pct DOUBLE PRECISION,
    oi_change_15m_pct DOUBLE PRECISION,
    oi_change_30m_pct DOUBLE PRECISION,
    oi_change_1h_pct DOUBLE PRECISION,
    oi_delta_pct DOUBLE PRECISION,
    price_change_5m_pct DOUBLE PRECISION,
    price_change_15m_pct DOUBLE PRECISION,
    price_change_30m_pct DOUBLE PRECISION,
    price_change_1h_pct DOUBLE PRECISION,
    global_ls_5m DOUBLE PRECISION,
    global_ls_15m DOUBLE PRECISION,
    global_ls_30m DOUBLE PRECISION,
    global_ls_1h DOUBLE PRECISION,
    long_account_pct DOUBLE PRECISION,
    short_account_pct DOUBLE PRECISION,
    top_ls DOUBLE PRECISION,
    top_ls_accounts DOUBLE PRECISION,
    top_ls_positions DOUBLE PRECISION,
    top_ls_delta_pct DOUBLE PRECISION,
    ls_delta_pct DOUBLE PRECISION,
    cvd DOUBLE PRECISION,
    z_ls_val DOUBLE PRECISION,
    z_score DOUBLE PRECISION,
    z_top_ls_acc DOUBLE PRECISION,
    z_top_ls_pos DOUBLE PRECISION,
    imbalance DOUBLE PRECISION,
    funding DOUBLE PRECISION,
    rsi DOUBLE PRECISION,
    vol_usd DOUBLE PRECISION,
    weighted_oi DOUBLE PRECISION,
    vpi DOUBLE PRECISION,
    zsc DOUBLE PRECISION,
    lsm DOUBLE PRECISION,
    updated_at TIMESTAMPTZ DEFAULT now(),
    raw_json JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS metrics_symbol_tf_idx
    ON metrics(symbol, timeframe, updated_at DESC);

CREATE TABLE IF NOT EXISTS quant_summary (
    id BIGSERIAL,
    symbol TEXT,
    timeframe TEXT,
    oi_z DOUBLE PRECISION,
    ls_delta_pct DOUBLE PRECISION,
    imbalance DOUBLE PRECISION,
    funding DOUBLE PRECISION,
    confluence_score DOUBLE PRECISION,
    bias TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS market_rest_metrics (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    trades INT,
    oi DOUBLE PRECISION,
    funding_rate DOUBLE PRECISION,
    mark_price DOUBLE PRECISION,
    global_long_short_ratio DOUBLE PRECISION,
    top_trader_long_short_ratio DOUBLE PRECISION,
    top_trader_account_ratio DOUBLE PRECISION,
    open_interest_hist_usd DOUBLE PRECISION,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

async def run_migrations():
    print(f"🔌 Connecting to {DATABASE_URL}")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Step 1: Ensure base schema
        await conn.execute(CREATE_TABLES_SQL)
        print("✅ Tables created or verified.")

        # Step 2: Ensure composite primary keys
        for table in ["metrics", "quant_summary"]:
            try:
                await conn.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey;")
                await conn.execute(f"ALTER TABLE {table} ADD CONSTRAINT {table}_pkey PRIMARY KEY (id, updated_at);")
                print(f"🔑 Primary key reset for {table} (id, updated_at).")
            except Exception as e:
                print(f"⚠️ Could not alter primary key for {table}: {e}")

        # Step 3: Create hypertables safely
        for table in ["metrics", "quant_summary"]:
            try:
                await conn.execute(f"""
                    SELECT create_hypertable(
                        '{table}',
                        'updated_at',
                        if_not_exists => TRUE,
                        migrate_data => TRUE
                    );
                """)
                print(f"📈 Hypertable ensured for {table}.")
            except Exception as e:
                print(f"⚠️ Skipped hypertable creation for {table}: {e}")

    finally:
        await conn.close()
        print("🔒 Connection closed.")

if __name__ == "__main__":
    asyncio.run(run_migrations())
