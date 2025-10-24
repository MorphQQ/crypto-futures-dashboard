# backend/src/futuresboard/db.py
"""
Async PostgreSQL DB layer for Crypto Futures Quant Platform

Features:
- Auto-create schema (safe CREATE IF NOT EXISTS)
- Unified configuration via config.get_settings()
- Bulk insert helpers for metrics, quant_features, diagnostics, signals, confluence, regimes, context_scores, context_trends
- Safe batching with fallback to single-row inserts
- insert_batch general-purpose helper (used by rest_collector)
- pruning utilities & background loop
- JSON sanitization helpers
- Detailed inline comments for audit / onboarding (Phase 4.5)
"""

from __future__ import annotations
import os
import asyncio
import logging
import math
import json
from typing import Any, Dict, List, Optional, Sequence
import asyncpg
from datetime import datetime, timedelta
from dateutil import parser as dateutil_parser

# The quant engine exports a helper safe_float used by some insert helpers
from .utils import safe_float

# unified config (pydantic settings)
from .config import get_settings
cfg = get_settings()

logger = logging.getLogger("futuresboard.db")
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------
# Database connection pool (asyncpg)
# ---------------------------------------------------------------------
DATABASE_URL = cfg.DATABASE_URL
POOL_MIN_SIZE = cfg.DB_POOL_MIN
POOL_MAX_SIZE = cfg.DB_POOL_MAX

_pool: Optional[asyncpg.pool.Pool] = None
_init_lock = asyncio.Lock()

# ---------------------------------------------------------------------
# DB Connection Safety Helpers
# ---------------------------------------------------------------------
async def is_connected() -> bool:
    """Return True if the DB pool exists and can respond to a simple ping."""
    global _pool
    if _pool is None:
        return False
    try:
        async with _pool.acquire() as conn:
            await conn.execute("SELECT 1;")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------
# DB Connection Safety Helpers
# ---------------------------------------------------------------------
async def ensure_connected(retries: int = 3, delay: float = 2.0) -> bool:
    """
    Ensure the asyncpg pool exists and is open.
    Reconnect automatically if missing or closed.
    Returns True if connected successfully.
    """
    global _pool

    # Quick check: already healthy
    try:
        closed_flag = getattr(_pool, "_closed", False)
    except Exception:
        closed_flag = True

    if _pool and not closed_flag:
        return True

    # We'll attempt to create/init the pool multiple times. init_db_async()
    # will acquire _init_lock itself so we must NOT hold it here.
    for attempt in range(1, retries + 1):
        try:
            logger.warning(f"[DB] Pool missing or closed — reconnecting (attempt {attempt}/{retries})...")
            # init_db_async acquires the _init_lock internally to serialise init.
            await init_db_async()
            if _pool and not getattr(_pool, "_closed", False):
                logger.info("[DB] ✅ Reconnected successfully")
                return True
        except Exception as e:
            logger.error(f"[DB] Reconnect attempt {attempt} failed: {e}", exc_info=True)
        # backoff
        await asyncio.sleep(delay)

    logger.error("[DB] ❌ Failed to reconnect after multiple attempts")
    return False



async def ensure_pool():
    """Alias for backward compatibility."""
    logger.debug("[DB] ensure_pool() redirecting to ensure_connected()")
    return await ensure_connected()


async def db_health_status() -> dict:
    """Return health summary for /api/db/health."""
    ok = await is_connected()
    stats = await get_pool_stats()
    return {
        "db_connected": ok,
        "timestamp": datetime.utcnow().isoformat(),
        "pool_status": stats,
    }

# ---------------------------------------------------------------------
# Column definitions & prepared SQL for metrics (keeps INSERT order strict)
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# Initialize DB pool and optionally create tables (auto-create mode)
# ---------------------------------------------------------------------
async def init_db_async():
    """
    Initialize asyncpg pool and create required tables if absent.
    This function is tolerant (CREATE IF NOT EXISTS) so R&D forks can iterate.
    """
    global _pool
    async with _init_lock:
        # If a pool already exists and isn't closed, just reuse it
        if _pool and not getattr(_pool, "_closed", True):
            return

        logger.info(f"[DB] connecting to database → {DATABASE_URL}")
        try:
            # wrap create_pool in a timeout so Windows async hang cannot occur
            _pool = await asyncio.wait_for(
                asyncpg.create_pool(
                    dsn=DATABASE_URL,
                    min_size=POOL_MIN_SIZE,
                    max_size=POOL_MAX_SIZE,
                ),
                timeout=10.0,  # hard timeout for network hangs
            )
            logger.info("[DB] connection pool created")
        except asyncio.TimeoutError:
            logger.error("[DB] ❌ Timeout while creating pool (10 s)")
            raise
        except Exception as e:
            logger.error(f"[DB] ❌ create_pool failed: {e}", exc_info=True)
            raise

        # Create tables/indexes if missing (auto-create behaviour for R&D)
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

            # market_rest_metrics (raw REST snapshots)
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

            # quant_summary (light dashboard summary)
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
            # Unique index for potential ON CONFLICT usage (keeps symbol+timeframe uniqueness intent)
            await conn.execute("""
            CREATE INDEX IF NOT EXISTS quant_summary_symbol_tf_idx
            ON quant_summary(symbol, timeframe, updated_at DESC);
            """)

            # quant_features (replay / record of computed metrics)
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

            # quant_features_5s (high-frequency table)
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

            # quant_diagnostics (rolling correlations & volatility)
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

            # quant_signals (family-level signals)
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

            # quant_confluence (aggregated confluence)
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

            # quant_regimes (market regimes)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS quant_regimes (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                ts TIMESTAMP WITH TIME ZONE DEFAULT now(),
                confluence_score DOUBLE PRECISION,
                volatility DOUBLE PRECISION,
                regime TEXT,
                confidence DOUBLE PRECISION,
                raw_json JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS quant_regimes_symbol_ts_idx
            ON quant_regimes(symbol, ts DESC);
            """)

            # quant_context_scores (context scoring)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS quant_context_scores (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                context_score DOUBLE PRECISION,
                bias TEXT,
                components JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS quant_context_scores_symbol_ts_idx
                ON quant_context_scores(symbol, ts DESC);
            """)

            # quant_context_trends (context transitions)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS quant_context_trends (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                ts TIMESTAMP WITH TIME ZONE DEFAULT now(),
                from_bias TEXT,
                to_bias TEXT,
                context_score DOUBLE PRECISION,
                raw_json JSONB DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS quant_context_trends_symbol_ts_idx
            ON quant_context_trends(symbol, ts DESC);
            """)

            logger.info("[DB] initialized and ready")

# ---------------------------------------------------------------------
# Close pool
# ---------------------------------------------------------------------
async def close_db_async():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("[DB] pool closed")

# ---------------------------------------------------------------------
# Simple DB ping (startup verification)
# ---------------------------------------------------------------------
async def verify_connection_async():
    """Simple DB ping for startup diagnostics."""
    await ensure_connected()
    try:
        async with _pool.acquire() as conn:
            await conn.execute("SELECT 1")
        logger.info("[DB] ✅ Connection verified")
        return True
    except Exception as e:
        logger.error(f"[DB] ❌ Connection test failed: {e}")
        return False

# ---------------------------------------------------------------------
# Helper utils
# ---------------------------------------------------------------------
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

def sanitize_json(obj):
    """Recursively replace NaN/Inf floats with None to make JSON safe."""
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_json(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj

# ---------------------------------------------------------------------
# save_metrics_v3_async - primary metrics ingestion (bulk batching)
# ---------------------------------------------------------------------
async def save_metrics_v3_async(metrics: List[Dict[str, Any]], timeframe: str = "1m") -> int:
    """
    Accepts list of metric dicts and writes them into `metrics` table in batches.
    Preserves older behavior: tolerate mixed field names (Price/price/etc.) and
    sanitize non-finite floats to NULL.
    """
    global _pool
    await ensure_connected()
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
                    # fallback to single-row inserts so other rows still persist
                    for row in chunk:
                        try:
                            await conn.execute(INSERT_SQL, *row)
                        except Exception as e2:
                            logger.warning(f"[save_metrics_v3_async] single insert failed: {e2}")
    return saved

# ---------------------------------------------------------------------
# Save computed quant features for replay/backtest (quant_features)
# ---------------------------------------------------------------------
async def save_quant_features_async(features: List[Dict[str, Any]]) -> int:
    """
    Persist computed quant metrics (1m) into quant_features for replay/backtesting.
    """
    global _pool
    await ensure_connected()
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

# ---------------------------------------------------------------------
# Save diagnostics
# ---------------------------------------------------------------------
async def save_quant_diagnostics_async(rows: list[dict[str, any]]) -> int:
    """Bulk insert diagnostics snapshots into quant_diagnostics."""
    if not rows:
        return 0
    global _pool
    await ensure_connected()

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

# ---------------------------------------------------------------------
# Save family-level signals
# ---------------------------------------------------------------------
async def save_quant_signals_async(rows: list[dict[str, any]]) -> int:
    """Bulk insert family signal scores."""
    if not rows:
        return 0
    global _pool
    await ensure_connected()

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

# ---------------------------------------------------------------------
# Save confluence snapshots
# ---------------------------------------------------------------------
async def save_quant_confluence_async(rows: list[dict[str, any]]) -> int:
    """Bulk insert confluence snapshots."""
    if not rows:
        return 0
    global _pool
    await ensure_connected()

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

# ---------------------------------------------------------------------
# Save regime classification results
# ---------------------------------------------------------------------
async def save_quant_regimes_async(rows: list[dict[str, any]]) -> int:
    """Bulk insert regime classification results."""
    if not rows:
        return 0
    global _pool
    await ensure_connected()

    sql = """
    INSERT INTO quant_regimes (
        symbol, ts, confluence_score, volatility,
        regime, confidence, raw_json
    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
    """
    count = 0
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                try:
                    await conn.execute(sql,
                        r.get("symbol"),
                        r.get("ts", datetime.utcnow()),
                        r.get("confluence_score"),
                        r.get("volatility"),
                        r.get("regime"),
                        r.get("confidence"),
                        json.dumps(sanitize_json(r.get("raw_json") or {}), default=str),
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"[save_quant_regimes_async] failed: {e}")
    logger.info(f"[DB] saved {count} rows into quant_regimes")
    return count

# ---------------------------------------------------------------------
# Save context transitions (quant_context_trends)
# ---------------------------------------------------------------------
async def save_quant_context_trends_async(rows: list[dict]):
    """Insert context bias transitions into quant_context_trends."""
    if not rows:
        return 0
    try:
        await ensure_connected()
        async with _pool.acquire() as conn:
            await conn.executemany("""
                INSERT INTO quant_context_trends (symbol, ts, from_bias, to_bias, context_score, raw_json)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, [
                (
                    r.get("symbol"),
                    r.get("ts"),
                    r.get("from_bias"),
                    r.get("to_bias"),
                    r.get("context_score"),
                    json.dumps(r.get("raw_json") or {})
                ) for r in rows
            ])
        logger.info(f"[DB] saved {len(rows)} rows into quant_context_trends")
        return len(rows)
    except Exception as e:
        logger.warning(f"[save_quant_context_trends_async] failed: {e}")
        return 0

# ---------------------------------------------------------------------
# General insert_batch helper (used by rest_collector)
# ---------------------------------------------------------------------
async def insert_batch(table: str, rows: list[dict]) -> int:
    """
    Generic batch insert for arbitrary table using row dict keys as columns.
    Will attempt ISO timestamp parsing for strings.
    """
    global _pool
    await ensure_connected()

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
                        # if dict/list -> JSON string
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

# ---------------------------------------------------------------------
# Helpers for app-level merges & queries
# ---------------------------------------------------------------------
async def get_latest_rest_metric(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Return the most recent row from market_rest_metrics for symbol (as dict),
    or None if missing.
    """
    global _pool
    await ensure_connected()
    q = "SELECT * FROM market_rest_metrics WHERE symbol = $1 ORDER BY ts DESC LIMIT 1"
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(q, symbol)
    if not row:
        return None
    return dict(row)

async def get_latest_metrics_async(limit: int = 100, tf: Optional[str] = None, symbol: Optional[str] = None):
    """
    Return latest metrics, optionally filtered by timeframe and/or symbol.
    """
    global _pool
    await ensure_connected()
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
    """
    Fetch metrics history for a given symbol (and optional timeframe).
    """
    global _pool
    await ensure_connected()
    if tf:
        q = "SELECT * FROM metrics WHERE symbol = $1 AND timeframe = $2 ORDER BY updated_at DESC LIMIT $3"
        async with _pool.acquire() as conn:
            rows = await conn.fetch(q, symbol, tf, limit)
    else:
        q = "SELECT * FROM metrics WHERE symbol = $1 ORDER BY updated_at DESC LIMIT $2"
        async with _pool.acquire() as conn:
            rows = await conn.fetch(q, symbol, limit)
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------
# Persist 5s quant features (high-frequency table)
# ---------------------------------------------------------------------
async def save_quant_features_5s_async(features: List[Dict[str, Any]]) -> int:
    """
    Persist 5s quant features into quant_features_5s.
    Bulk inserts in batches; uses datetime.utcnow() for ts if not present.
    """
    global _pool
    await ensure_connected()
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

# ---------------------------------------------------------------------
# Robust context score saver (one canonical implementation)
# ---------------------------------------------------------------------
async def save_quant_context_scores_async(rows: list[dict]):
    """
    Persist context scoring rows into quant_context_scores (safe bulk insert).
    This version logs and continues on per-row errors to avoid transaction aborts
    for a single malformed row.
    """
    global _pool
    await ensure_connected()
    if not rows:
        return 0

    sql = """
        INSERT INTO quant_context_scores (symbol, ts, context_score, bias, components)
        VALUES ($1, $2, $3, $4, $5)
    """
    count = 0
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                try:
                    components_json = json.dumps(sanitize_json(r.get("components") or {}), default=str)
                    ts = r.get("ts") or datetime.utcnow()
                    await conn.execute(
                        sql,
                        r.get("symbol"),
                        ts,
                        safe_float(r.get("context_score")),
                        r.get("bias"),
                        components_json
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"[save_quant_context_scores_async] insert failed for {r.get('symbol')}: {e}")
    logger.info(f"[DB] saved {count} rows into quant_context_scores")
    return count

# ---------------------------------------------------------------------
# Prune helpers
# ---------------------------------------------------------------------
async def prune_old_data(days: int = 60, tables: Optional[List[str]] = None, dry_run: bool = False) -> Dict[str, int]:
    """
    Prune rows older than `days` from the provided tables.
    Returns a dict mapping table -> rows_deleted (or rows_matched if dry_run=True).
    Default: prune quant_features_5s only.
    """
    global _pool
    await ensure_connected()
    if tables is None:
        tables = ["quant_features_5s"]
    cutoff = datetime.utcnow() - timedelta(days=days)
    results: Dict[str, int] = {}
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for tbl in tables:
                try:
                    if dry_run:
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
    Designed to be run as a background task from app startup.
    """
    if tables is None:
        tables = ["quant_features_5s"]
    logger.info(f"[prune_old_data_loop] starting: every {interval_hours}h prune older than {days} days for tables={tables}")
    try:
        while True:
            try:
                await prune_old_data(days=days, tables=tables, dry_run=False)
            except Exception as e:
                logger.exception(f"[prune_old_data_loop] iteration failed: {e}")
            await asyncio.sleep(interval_hours * 3600)
    except asyncio.CancelledError:
        logger.info("[prune_old_data_loop] cancelled — exiting")
        raise

# ---------------------------------------------------------------------
# Monitoring helper
# ---------------------------------------------------------------------
async def get_pool_stats():
    """Return basic stats about current connection pool."""
    global _pool
    if not _pool or getattr(_pool, "_closed", True):
        return {"status": "disconnected"}
    holders = getattr(_pool, "_holders", [])
    return {
        "status": "connected",
        "min": getattr(_pool, "_minsize", None),
        "max": getattr(_pool, "_maxsize", None),
        "size": len(holders),
        "idle": sum(1 for h in holders if h._con is not None and not h._in_use),
    }

class DBConnection:
    """Async context manager that ensures the pool exists before acquiring."""
    def __init__(self):
        self.conn = None

    async def __aenter__(self):
        await ensure_connected()
        global _pool
        self.conn = await _pool.acquire()
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        global _pool
        if self.conn:
            await _pool.release(self.conn)
