import asyncio
import asyncpg
from tabulate import tabulate

DSN = "postgresql://postgres:postgres@localhost:5432/futures"

async def show_latest():
    conn = await asyncpg.connect(DSN)

    print("\n=== metrics (latest 3) ===")
    rows = await conn.fetch("""
        SELECT symbol, price, oi_usd, global_ls_5m, funding, updated_at
        FROM metrics
        ORDER BY updated_at DESC
        LIMIT 3
    """)
    print(tabulate([dict(r) for r in rows], headers="keys"))

    print("\n=== quant_features (latest 3) ===")
    rows = await conn.fetch("""
        SELECT symbol, zsc, confidence, atr_5s, obi, vpi, ts
        FROM quant_features
        ORDER BY ts DESC
        LIMIT 3
    """)
    print(tabulate([dict(r) for r in rows], headers="keys"))

    print("\n=== market_rest_metrics (latest 3) ===")
    rows = await conn.fetch("""
        SELECT symbol, funding_rate, global_long_short_ratio, open_interest_hist_usd, ts
        FROM market_rest_metrics
        ORDER BY ts DESC
        LIMIT 3
    """)
    print(tabulate([dict(r) for r in rows], headers="keys"))

    await conn.close()

asyncio.run(show_latest())
