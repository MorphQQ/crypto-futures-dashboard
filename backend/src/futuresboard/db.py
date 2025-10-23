# backend/src/futuresboard/db.py
from __future__ import annotations
import os
import asyncio
import logging
import math
from typing import List, Optional, Any, Dict, Sequence
import asyncpg
import json
from datetime import datetime, timedelta
from dateutil import parser as dateutil_parser
from .quant_engine import safe_float

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

# Initialize DB pool and tables
async def init_db_async():
    global _pool
    async with _init_lock:
        if _pool:
            return
        logger.info("[DB] connecting to database")
        _pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE)
        # create tables if not present and helper indexes
        async with _pool.acquire() as conn:
            # metrics + index
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id BIGSERIAL PRIMARY KEY,
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
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                raw_json JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS metrics_symbol_tf_idx ON metrics(symbol, timeframe, updated_at DESC);
            """)
            # market_rest_metrics table (raw REST samples)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS market_rest_metrics (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMP WITH TIME ZONE,
                symbol TEXT,
                open DOUBLE PRECISION,
                high DOUBLE PRECISION,
                low DOUBLE PRECISION,
                close DOUBLE PRECISION,
                volume DOUBLE PRECISION,
                trades INTEGER,
                oi DOUBLE PRECISION,
                funding_rate DOUBLE PRECISION,
                mark_price DOUBLE PRECISION,
                global_long_short_ratio DOUBLE PRECISION,
                top_trader_long_short_ratio DOUBLE PRECISION,
                top_trader_account_ratio DOUBLE PRECISION,
                open_interest_hist_usd DOUBLE PRECISION,
                metadata JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS market_rest_metrics_symbol_ts_idx ON market_rest_metrics(symbol, ts DESC);
            """)
            # quant_summary + unique index for ON CONFLICT
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS quant_summary (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT,
                timeframe TEXT,
                oi_z DOUBLE PRECISION,
                ls_delta_pct DOUBLE PRECISION,
                imbalance DOUBLE PRECISION,
                funding DOUBLE PRECISION,
                confluence_score DOUBLE PRECISION,
                bias TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            """)
            # unique index ensures INSERT ... ON CONFLICT(symbol, timeframe) works
            await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS quant_summary_symbol_tf_idx
            ON quant_summary(symbol, timeframe, updated_at);
            """)
            # quant_features: stores all live computed quant metrics (for replay)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS quant_features (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '1m',
                ts TIMESTAMP WITH TIME ZONE DEFAULT now(),
                price DOUBLE PRECISION,
                oi_usd DOUBLE PRECISION,
                vol_usd DOUBLE PRECISION,
                global_ls_5m DOUBLE PRECISION,
                top_ls_accounts DOUBLE PRECISION,
                top_ls_positions DOUBLE PRECISION,
                funding DOUBLE PRECISION,
                atr_5s DOUBLE PRECISION,
                obi DOUBLE PRECISION,
                taker_buy_ratio DOUBLE PRECISION,
                taker_sell_ratio DOUBLE PRECISION,
                vpi DOUBLE PRECISION,
                z_oi DOUBLE PRECISION,
                z_top_ls_acc DOUBLE PRECISION,
                z_obi DOUBLE PRECISION,
                z_funding DOUBLE PRECISION,
                zsc DOUBLE PRECISION,
                oi_change_5s_pct DOUBLE PRECISION,
                oi_change_10s_pct DOUBLE PRECISION,
                price_change_5s_pct DOUBLE PRECISION,
                price_change_10s_pct DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                families JSONB DEFAULT '{}'::jsonb,
                raw_json JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS quant_features_symbol_ts_idx
            ON quant_features(symbol, ts DESC);
            """)
            # quant_features_5s: high-frequency (5s) derived features
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS quant_features_5s (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '5s',
                ts TIMESTAMP WITH TIME ZONE DEFAULT now(),
                price DOUBLE PRECISION,
                oi_usd DOUBLE PRECISION,
                vol_usd DOUBLE PRECISION,
                oi_change_5s_pct DOUBLE PRECISION,
                oi_change_10s_pct DOUBLE PRECISION,
                price_change_5s_pct DOUBLE PRECISION,
                price_change_10s_pct DOUBLE PRECISION,
                atr_5s DOUBLE PRECISION,
                obi DOUBLE PRECISION,
                taker_buy_ratio DOUBLE PRECISION,
                taker_sell_ratio DOUBLE PRECISION,
                vpi DOUBLE PRECISION,
                z_oi DOUBLE PRECISION,
                z_top_ls_acc DOUBLE PRECISION,
                z_obi DOUBLE PRECISION,
                z_funding DOUBLE PRECISION,
                zsc DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                families JSONB DEFAULT '{}'::jsonb,
                raw_json JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS quant_features_5s_symbol_ts_idx
            ON quant_features_5s(symbol, ts DESC);
            """)
            # quant_diagnostics: rolling correlations & validation
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS quant_diagnostics (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                ts TIMESTAMP WITH TIME ZONE DEFAULT now(),
                window_s INT DEFAULT 60,
                corr_price_oi DOUBLE PRECISION,
                corr_price_ls DOUBLE PRECISION,
                corr_oi_ls DOUBLE PRECISION,
                volatility_5s DOUBLE PRECISION,
                volatility_zscore DOUBLE PRECISION,
                confluence_density DOUBLE PRECISION,
                raw_json JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS quant_diagnostics_symbol_ts_idx
            ON quant_diagnostics(symbol, ts DESC);
            """)
            # quant_signals: summarized family-level signals
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS quant_signals (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                ts TIMESTAMP WITH TIME ZONE DEFAULT now(),
                family TEXT,
                score DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                diagnostics_ref BIGINT,
                raw_json JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS quant_signals_symbol_ts_idx
            ON quant_signals(symbol, ts DESC);
            """)
            # quant_confluence: aggregated regime & confidence score
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS quant_confluence (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                ts TIMESTAMP WITH TIME ZONE DEFAULT now(),
                confluence_score DOUBLE PRECISION,
                bull_strength DOUBLE PRECISION,
                bear_strength DOUBLE PRECISION,
                volatility DOUBLE PRECISION,
                family_count INT,
                diagnostic_ref BIGINT,
                raw_json JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS quant_confluence_symbol_ts_idx
            ON quant_confluence(symbol, ts DESC);
            """)
        logger.info("[DB] initialized and ready")


async def close_db_async():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("[DB] pool closed")


# Utility to coerce numbers
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


# Save metrics (list of dicts) batching with executemany
async def save_metrics_v3_async(metrics: List[Dict[str, Any]], timeframe: str = "1m") -> int:
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
                # accept upper/lower variants
                return _safe_num(m.get(k, fallback))
            updated_at = datetime.utcnow()
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
            # sanitize non-finite floats
            for idx, v in enumerate(row):
                if isinstance(v, float) and not math.isfinite(v):
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

# -------------------------------------------------------------------
# Save quant-enriched features (from quant_engine)
# -------------------------------------------------------------------
async def save_quant_features_async(features: List[Dict[str, Any]]) -> int:
    """
    Persists computed quant features (from quant_engine.compute_quant_metrics)
    into quant_features table for replay/backtest.
    """
    global _pool
    if not _pool:
        await init_db_async()
    if not features:
        return 0

    cols = [
        "symbol", "timeframe", "ts", "price", "oi_usd", "vol_usd",
        "global_ls_5m", "top_ls_accounts", "top_ls_positions", "funding",
        "atr_5s", "obi", "taker_buy_ratio", "taker_sell_ratio", "vpi",
        "z_oi", "z_top_ls_acc", "z_obi", "z_funding", "zsc",
        "oi_change_5s_pct", "oi_change_10s_pct",
        "price_change_5s_pct", "price_change_10s_pct",
        "confidence", "families", "raw_json"
    ]
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    sql = f"INSERT INTO quant_features ({', '.join(cols)}) VALUES ({placeholders})"
    count = 0

    async with _pool.acquire() as conn:
        async with conn.transaction():
            for f in features:
                try:
                    values = [
                        f.get("symbol"),
                        f.get("timeframe", "1m"),
                        datetime.utcnow(),
                        f.get("price"),
                        f.get("oi_usd"),
                        f.get("vol_usd"),
                        f.get("global_ls_5m"),
                        f.get("top_ls_accounts"),
                        f.get("top_ls_positions"),
                        f.get("funding"),
                        f.get("atr_5s"),
                        f.get("obi"),
                        f.get("taker_buy_ratio"),
                        f.get("taker_sell_ratio"),
                        f.get("vpi"),
                        f.get("z_oi"),
                        f.get("z_top_ls_acc"),
                        f.get("z_obi"),
                        f.get("z_funding"),
                        f.get("zsc"),
                        f.get("oi_change_5s_pct"),
                        f.get("oi_change_10s_pct"),
                        f.get("price_change_5s_pct"),
                        f.get("price_change_10s_pct"),
                        f.get("confidence"),
                        json.dumps(f.get("families") or {}),
                        json.dumps(f)
                    ]
                    await conn.execute(sql, *values)
                    count += 1
                except Exception as e:
                    logger.warning(f"[save_quant_features_async] insert failed: {e}")
    logger.info(f"[DB.save_quant_features_async] saved {count} quant feature rows")
    return count

async def save_quant_diagnostics_async(rows: list[dict[str, any]]) -> int:
    """Bulk insert diagnostics snapshots into quant_diagnostics."""
    if not rows:
        return 0
    global _pool
    if not _pool:
        await init_db_async()

    cols = [
        "symbol", "ts", "window_s",
        "corr_price_oi", "corr_price_ls", "corr_oi_ls",
        "volatility_5s", "volatility_zscore",
        "confluence_density", "raw_json"
    ]
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    sql = f"INSERT INTO quant_diagnostics ({', '.join(cols)}) VALUES ({placeholders})"

    count = 0
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                try:
                    await conn.execute(sql,
                        r.get("symbol"),
                        r.get("ts"),
                        r.get("window_s", 60),
                        r.get("corr_price_oi"),
                        r.get("corr_price_ls"),
                        r.get("corr_oi_ls"),
                        r.get("volatility_5s"),
                        r.get("volatility_zscore"),
                        r.get("confluence_density"),
                        json.dumps(sanitize_json(r.get("raw_json") or {}), default=str),
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"[save_quant_diagnostics_async] failed: {e}")
    logger.info(f"[DB] saved {count} rows into quant_diagnostics")
    return count

async def save_quant_signals_async(rows: list[dict[str, any]]) -> int:
    """Bulk insert family signal scores."""
    if not rows:
        return 0
    global _pool
    if not _pool:
        await init_db_async()

    cols = [
        "symbol", "ts", "family",
        "score", "confidence",
        "diagnostics_ref", "raw_json"
    ]
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    sql = f"INSERT INTO quant_signals ({', '.join(cols)}) VALUES ({placeholders})"

    count = 0
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                try:
                    await conn.execute(sql,
                        r.get("symbol"),
                        r.get("ts"),
                        r.get("family"),
                        r.get("score"),
                        r.get("confidence"),
                        r.get("diagnostics_ref"),
                        json.dumps(sanitize_json(r.get("raw_json") or {}), default=str),
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"[save_quant_signals_async] failed: {e}")
    logger.info(f"[DB] saved {count} rows into quant_signals")
    return count

async def save_quant_confluence_async(rows: list[dict[str, any]]) -> int:
    """Bulk insert confluence snapshots."""
    if not rows:
        return 0
    global _pool
    if not _pool:
        await init_db_async()

    sql = """
    INSERT INTO quant_confluence (
        symbol, ts, confluence_score, bull_strength,
        bear_strength, volatility, family_count,
        diagnostic_ref, raw_json
    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
    """
    count = 0
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                try:
                    await conn.execute(sql,
                        r.get("symbol"),
                        r.get("ts"),
                        r.get("confluence_score"),
                        r.get("bull_strength"),
                        r.get("bear_strength"),
                        r.get("volatility"),
                        r.get("family_count"),
                        r.get("diagnostic_ref"),
                        json.dumps(sanitize_json(r.get("raw_json") or {}), default=str),
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"[save_quant_confluence_async] failed: {e}")
    logger.info(f"[DB] saved {count} rows into quant_confluence")
    return count

# -------------------------------------------------------------------
# Generic async batch insert (used by rest_collector)
# -------------------------------------------------------------------
async def insert_batch(table: str, rows: list[dict]) -> int:
    global _pool
    if not _pool:
        await init_db_async()

    if not rows:
        logger.debug(f"[DB.insert_batch] no rows provided for {table}")
        return 0

    cols = list(rows[0].keys())
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    count = 0

    async with _pool.acquire() as conn:
        async with conn.transaction():
            for row in rows:
                try:
                    values = []
                    for v in row.values():
                        # if dict/list -> JSON
                        if isinstance(v, (dict, list)):
                            values.append(json.dumps(v, default=str))
                        # if string looks like ISO timestamp, try to parse to datetime
                        elif isinstance(v, str):
                            parsed = None
                            try:
                                parsed = dateutil_parser.isoparse(v)
                            except Exception:
                                parsed = None
                            if parsed:
                                values.append(parsed)
                            else:
                                values.append(v)
                        else:
                            values.append(v)
                    await conn.execute(sql, *values)
                    count += 1
                except Exception as e:
                    logger.warning(f"[DB.insert_batch] insert failed for {table}: {e}")
    logger.info(f"[DB.insert_batch] inserted {count} rows into {table}")
    return count


# -------------------------------------------------------------------
# Helpers for app-level merges
# -------------------------------------------------------------------
async def get_latest_rest_metric(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Return the most recent row from market_rest_metrics for symbol (as dict),
    or None if missing.
    """
    global _pool
    if not _pool:
        await init_db_async()
    q = "SELECT * FROM market_rest_metrics WHERE symbol = $1 ORDER BY ts DESC LIMIT 1"
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(q, symbol)
    if not row:
        return None
    return dict(row)


# -------------------------------------------------------------------
# Query helpers for metrics retrieval
# -------------------------------------------------------------------
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

async def save_quant_features_5s_async(features: List[Dict[str, Any]]) -> int:
    """
    Persist 5s quant features into quant_features_5s.
    Expects a list of dicts produced by compute_quant_metrics (or similar).
    Bulk inserts in batches; uses datetime.utcnow() for ts if not present.
    """
    global _pool
    if not _pool:
        await init_db_async()
    if not features:
        return 0

    cols = [
        "symbol", "timeframe", "ts", "price", "oi_usd", "vol_usd",
        "oi_change_5s_pct", "oi_change_10s_pct", "price_change_5s_pct", "price_change_10s_pct",
        "atr_5s", "obi", "taker_buy_ratio", "taker_sell_ratio", "vpi",
        "z_oi", "z_top_ls_acc", "z_obi", "z_funding", "zsc",
        "confidence", "families", "raw_json"
    ]
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    sql = f"INSERT INTO quant_features_5s ({', '.join(cols)}) VALUES ({placeholders})"
    batch = int(os.getenv("DB_INSERT_BATCH", "200"))
    count = 0

    async with _pool.acquire() as conn:
        async with conn.transaction():
            for i in range(0, len(features), batch):
                chunk = features[i:i+batch]
                values_list = []
                for f in chunk:
                    ts = f.get("ts") or datetime.utcnow()
                    # ensure JSON serializable for families / raw_json
                    families = f.get("families") or {}
                    raw_json = f.get("raw_json") or f
                    row = [
                        f.get("symbol"),
                        f.get("timeframe", "5s"),
                        ts,
                        safe_float(f.get("price")),
                        safe_float(f.get("oi_usd")),
                        safe_float(f.get("vol_usd")),
                        safe_float(f.get("oi_change_5s_pct")),
                        safe_float(f.get("oi_change_10s_pct")),
                        safe_float(f.get("price_change_5s_pct")),
                        safe_float(f.get("price_change_10s_pct")),
                        safe_float(f.get("atr_5s")),
                        safe_float(f.get("obi")),
                        safe_float(f.get("taker_buy_ratio")),
                        safe_float(f.get("taker_sell_ratio")),
                        safe_float(f.get("vpi")),
                        safe_float(f.get("z_oi")),
                        safe_float(f.get("z_top_ls_acc")),
                        safe_float(f.get("z_obi")),
                        safe_float(f.get("z_funding")),
                        safe_float(f.get("zsc")),
                        safe_float(f.get("confidence")),
                        json.dumps(families),
                        json.dumps(raw_json, default=str)
                    ]
                    # sanitize non-finite floats
                    for idx, v in enumerate(row):
                        if isinstance(v, float) and not math.isfinite(v):
                            row[idx] = None
                    values_list.append(row)
                try:
                    await conn.executemany(sql, values_list)
                    count += len(values_list)
                except Exception as e:
                    logger.warning(f"[save_quant_features_5s_async] bulk insert failed: {e}")
                    # fallback to single-row insert attempts
                    for row in values_list:
                        try:
                            await conn.execute(sql, *row)
                            count += 1
                        except Exception as e2:
                            logger.warning(f"[save_quant_features_5s_async] single insert failed: {e2}")
    logger.info(f"[DB.save_quant_features_5s_async] saved {count} rows to quant_features_5s")
    return count


async def prune_old_data(days: int = 60, tables: Optional[List[str]] = None, dry_run: bool = False) -> Dict[str, int]:
    """
    Prune rows older than `days` from the provided tables.
    Returns a dict mapping table -> rows_deleted (or rows_matched if dry_run=True).
    By default prunes quant_features_5s only (safe).
    """
    global _pool
    if tables is None:
        tables = ["quant_features_5s"]
    if not _pool:
        await init_db_async()

    cutoff = datetime.utcnow() - timedelta(days=days)
    results: Dict[str, int] = {}
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for tbl in tables:
                try:
                    if dry_run:
                        # count rows that would be deleted
                        q = f"SELECT COUNT(1) FROM {tbl} WHERE ts < $1"
                        rec = await conn.fetchval(q, cutoff)
                        results[tbl] = int(rec or 0)
                    else:
                        q = f"DELETE FROM {tbl} WHERE ts < $1"
                        res = await conn.execute(q, cutoff)
                        # asyncpg returns strings like 'DELETE <n>'
                        if isinstance(res, str) and res.startswith("DELETE"):
                            try:
                                n = int(res.split()[-1])
                            except Exception:
                                n = 0
                        else:
                            n = 0
                        results[tbl] = n
                except Exception as e:
                    logger.warning(f"[prune_old_data] failed for {tbl}: {e}")
                    results[tbl] = -1
    logger.info(f"[prune_old_data] prune result: {results} (dry_run={dry_run})")
    return results


async def prune_old_data_loop(interval_hours: int = 6, days: int = 60, tables: Optional[List[str]] = None):
    """
    Background loop to prune old high-frequency tables every `interval_hours`.
    Run this as a background task from app startup.
    """
    if tables is None:
        tables = ["quant_features_5s"]
    logger.info(f"[prune_old_data_loop] starting: every {interval_hours}h prune older than {days} days for tables={tables}")
    try:
        while True:
            try:
                # call prune (non-dry run)
                await prune_old_data(days=days, tables=tables, dry_run=False)
            except Exception as e:
                logger.exception(f"[prune_old_data_loop] iteration failed: {e}")
            await asyncio.sleep(interval_hours * 3600)
    except asyncio.CancelledError:
        logger.info("[prune_old_data_loop] cancelled — exiting")
        raise
    
def sanitize_json(obj):
    """Recursively replace NaN and Infinity with None for JSON serialization."""
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_json(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj