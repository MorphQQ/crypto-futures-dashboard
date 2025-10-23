"""
Postgres async DB layer using asyncpg connection pool.

Assumptions:
- Migrations are handled externally (TimescaleDB + CREATE TABLE done by migrations).
- This module ensures pool initialization, provides save/get helpers, and validates that
  required tables exist at startup (error if not).
"""
from __future__ import annotations
import os
import asyncio
import logging
import math
import json
from typing import List, Optional, Any, Dict, Sequence
import asyncpg
from datetime import datetime, timezone

logger = logging.getLogger("futuresboard.db")
logger.setLevel(logging.INFO)

DATABASE_URL = os.getenv("DATABASE_URL", os.getenv("DB_DSN", "postgresql://postgres:postgres@localhost:5432/futures"))
POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX", "10"))

_pool: Optional[asyncpg.pool.Pool] = None
_init_lock = asyncio.Lock()

# Columns order used by save_metrics_v3_async (must match INSERT order)
COLS = [
    "symbol",
    "timeframe",
    "price",
    "price_change_24h_pct",
    "volume_24h",
    "volume_change_24h_pct",
    "market_cap",
    "oi_usd",
    "oi_abs_usd",
    "oi_change_24h_pct",
    "oi_change_5m_pct",
    "oi_change_15m_pct",
    "oi_change_30m_pct",
    "oi_change_1h_pct",
    "oi_delta_pct",
    "price_change_5m_pct",
    "price_change_15m_pct",
    "price_change_30m_pct",
    "price_change_1h_pct",
    "global_ls_5m",
    "global_ls_15m",
    "global_ls_30m",
    "global_ls_1h",
    "long_account_pct",
    "short_account_pct",
    "top_ls",
    "top_ls_accounts",
    "top_ls_positions",
    "top_ls_delta_pct",
    "ls_delta_pct",
    "cvd",
    "z_ls_val",
    "z_score",
    "z_top_ls_acc",
    "z_top_ls_pos",
    "imbalance",
    "funding",
    "rsi",
    "vol_usd",
    "weighted_oi",
    "vpi",
    "zsc",
    "lsm",
    "updated_at",
    "raw_json"
]

INSERT_SQL = f"""
INSERT INTO metrics ({','.join(COLS)})
VALUES ({','.join(f'${i+1}' for i in range(len(COLS)))})
"""

# ---------- pool / init ----------
async def init_db_async(check_tables: bool = True):
    """
    Initialize the asyncpg pool and optionally verify required tables exist.
    NOTE: This function will not attempt to create tables. Use your migration tooling.
    """
    global _pool
    async with _init_lock:
        if _pool:
            return
        logger.info("[DB] connecting to database")
        _pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE)
        logger.info("[DB] pool created")
        if check_tables:
            await _validate_schema()

async def _validate_schema():
    """Ensure required tables/columns exist. Raise helpful error if not."""
    global _pool
    if not _pool:
        raise RuntimeError("DB pool not initialized")
    async with _pool.acquire() as conn:
        # Check metrics table
        try:
            r = await conn.fetchval(
                "SELECT to_regclass('public.metrics')"
            )
            if not r:
                raise RuntimeError("Required table 'metrics' not found. Run migrations.")
        except Exception as e:
            logger.error("[DB] schema validation failed: %s", e)
            raise

async def close_db_async():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("[DB] pool closed")

# ---------- utils ----------
def _safe_num(x):
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        return float(str(x).replace(",", "").replace("$", ""))
    except Exception:
        return None

def _safe_json(obj):
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return "{}"

# ---------- save API ----------
async def save_metrics_v3_async(metrics: List[Dict[str, Any]], timeframe: str = "1m") -> int:
    """
    Accepts list of metric dicts; produces rows matching COLS, writes to Postgres in batches.
    Returns number of saved rows.
    """
    global _pool
    if not _pool:
        await init_db_async()
    if not metrics:
        return 0

    values: List[Sequence[Any]] = []
    saved = 0
    for m in metrics:
        try:
            if not isinstance(m, dict):
                logger.debug("[save_metrics_v3_async] skip non-dict")
                continue
            symbol = (m.get("symbol") or m.get("sym") or "").strip()
            if not symbol:
                logger.debug("[save_metrics_v3_async] skip missing symbol")
                continue
            def g(k, fallback=None):
                return _safe_num(m.get(k, fallback))
            updated_at = datetime.now(timezone.utc)
            row = [
                symbol,
                timeframe,
                g("Price") or g("price") or None,
                g("price_change_24h_pct"),
                g("volume_24h"),
                g("volume_change_24h_pct"),
                g("market_cap"),
                g("oi_usd"),
                g("oi_abs_usd"),
                g("oi_change_24h_pct"),
                g("oi_change_5m_pct"),
                g("oi_change_15m_pct"),
                g("oi_change_30m_pct"),
                g("oi_change_1h_pct"),
                g("oi_delta_pct"),
                g("price_change_5m_pct"),
                g("price_change_15m_pct"),
                g("price_change_30m_pct"),
                g("price_change_1h_pct"),
                g("Global_LS_5m") or g("global_ls_5m"),
                g("Global_LS_15m") or g("global_ls_15m"),
                g("Global_LS_30m") or g("global_ls_30m"),
                g("Global_LS_1h") or g("global_ls_1h"),
                g("long_account_pct"),
                g("short_account_pct"),
                g("Top_LS") or g("top_ls"),
                g("Top_LS_Accounts") or g("top_ls_accounts"),
                g("Top_LS_Positions") or g("top_ls_positions"),
                g("top_ls_delta_pct"),
                g("ls_delta_pct"),
                g("cvd"),
                g("z_ls_val"),
                g("z_score"),
                g("z_top_ls_acc"),
                g("z_top_ls_pos"),
                g("imbalance"),
                g("funding"),
                g("rsi"),
                g("vol_usd") or g("volume"),
                g("weighted_oi"),
                g("vpi"),
                g("zsc"),
                g("lsm"),
                updated_at,
                _safe_json(m)
            ]
            # Coerce non-finite floats to None
            for idx, v in enumerate(row):
                if isinstance(v, float) and (not math.isfinite(v)):
                    row[idx] = None
            values.append(row)
            saved += 1
        except Exception as e:
            logger.warning(f"[save_metrics_v3_async] row prepare failed: {e}")

    if not values:
        return 0

    batch = int(os.getenv("DB_INSERT_BATCH", "200"))
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for i in range(0, len(values), batch):
                chunk = values[i:i+batch]
                try:
                    await conn.executemany(INSERT_SQL, chunk)
                except Exception as e:
                    logger.warning(f"[save_metrics_v3_async] batch insert failed ({len(chunk)}): {e}")
                    for row in chunk:
                        try:
                            await conn.execute(INSERT_SQL, *row)
                        except Exception as e2:
                            logger.warning(f"[save_metrics_v3_async] single insert failed: {e2}")
    return saved

# ---------- query helpers ----------
async def get_latest_metrics_async(limit: int = 100, tf: Optional[str] = None, symbol: Optional[str] = None):
    global _pool
    if not _pool:
        await init_db_async()
    where_clauses = []
    params = []
    if tf:
        where_clauses.append("timeframe = $%d" % (len(params) + 1))
        params.append(tf)
    if symbol:
        where_clauses.append("symbol = $%d" % (len(params) + 1))
        params.append(symbol)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    q = f"SELECT * FROM metrics {where} ORDER BY updated_at DESC LIMIT $%d" % (len(params) + 1)
    params.append(limit)
    async with _pool.acquire() as conn:
        rows = await conn.fetch(q, *params)
    return [dict(r) for r in rows]

async def get_metrics_by_symbol_async(symbol: str, limit: int = 100, tf: Optional[str] = None):
    global _pool
    if not _pool:
        await init_db_async()
    if tf:
        q = "SELECT * FROM metrics WHERE symbol = $1 AND timeframe = $2 ORDER BY updated_at DESC LIMIT $3"
        async with _pool.acquire() as conn:
            rows = await conn.fetch(q, symbol, tf, limit)
    else:
        q = "SELECT * FROM metrics WHERE symbol = $1 ORDER BY updated_at DESC LIMIT $2"
        async with _pool.acquire() as conn:
            rows = await conn.fetch(q, symbol, limit)
    return [dict(r) for r in rows]
