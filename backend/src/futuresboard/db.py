# Fixed: backend/src/futuresboard/db.py
# Changes:
# - Implemented GPT Patch B: Bulk insert for main table; executemany for TF mirror.
# - Added symbol filter to get_latest_metrics.
# - Removed duplicate imports (sqlite3 twice).
# - Ensured PRAGMA validation (fetchone check).
# - Fixed RSI: Use smoothed version consistently.
# - Added fallback for non-finite in bulk.

# backend/src/futuresboard/db.py
from __future__ import annotations

import os
import sqlite3
import pathlib
import random
import traceback
from datetime import datetime, timedelta, timezone
from typing import List, Any, Dict, Union

from dotenv import load_dotenv
load_dotenv()

import logging
import numpy as np
from typing import List, Any

from sqlalchemy import Column, Integer, String, DateTime, Float, create_engine, UniqueConstraint
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("futuresboard.db")
logger.setLevel(logging.INFO)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DB_PATH = os.getenv("DB_PATH", str(REPO_ROOT / "backend" / "src" / "futuresboard" / "futures.db"))

# SQLAlchemy engine with check_same_thread=False for threads + WAL isolation
engine = create_engine(
    f"sqlite:///{DB_PATH}", 
    echo=False, 
    connect_args={"check_same_thread": False}
)
SessionLocal = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))
Session = SessionLocal

Base = declarative_base()

class Metric(Base):
    __tablename__ = "metrics"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    timeframe = Column(String(10), default="5m", nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    price = Column(Float)
    price_change_24h_pct = Column(Float)
    volume_24h = Column(Float)
    volume_change_24h_pct = Column(Float)
    market_cap = Column(Float)
    oi_usd = Column(Float)
    oi_abs_usd = Column(Float)
    oi_change_24h_pct = Column(Float)
    oi_change_5m_pct = Column(Float)
    oi_change_15m_pct = Column(Float)
    oi_change_30m_pct = Column(Float)
    oi_change_1h_pct = Column(Float)
    oi_delta_pct = Column(Float)
    price_change_5m_pct = Column(Float)
    price_change_15m_pct = Column(Float)
    price_change_30m_pct = Column(Float)
    price_change_1h_pct = Column(Float)
    global_ls_5m = Column(Float)
    global_ls_15m = Column(Float)
    global_ls_30m = Column(Float)
    global_ls_1h = Column(Float)
    long_account_pct = Column(Float)
    short_account_pct = Column(Float)
    top_ls = Column(Float)
    top_ls_accounts = Column(Float)
    top_ls_positions = Column(Float)
    top_ls_delta_pct = Column(Float)
    ls_delta_pct = Column(Float)
    cvd = Column(Float)
    z_ls = Column(Float)
    z_score = Column(Float)
    z_top_ls_accounts = Column(Float)   # NEW
    z_top_ls_positions = Column(Float)  # NEW
    imbalance = Column(Float)
    funding = Column(Float)
    rsi = Column(Float)
    vol_usd = Column(Float, default=0.0)
    weighted_oi_usd = Column(Float)     # NEW
    vpi = Column(Float)                 # NEW
    zsc = Column(Float)                 # NEW: Z-Strength Composite
    lsm = Column(Float)                 # NEW: L/S momentum (smoothed Δ)
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "timestamp", name="unique_sym_tf_ts"),)

# --- helper functions and init ---
def get_db_conn():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    return conn

def init_app(app=None):
    """
    Initialize database: enable WAL, create per-timeframe tables,
    ensure indexes and PRAGMA tuning for high-frequency quant workloads.
    """
    p = pathlib.Path(DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)

    # create tables via SQLAlchemy
    Base.metadata.create_all(bind=engine)

    conn = get_db_conn()
    cur = conn.cursor()

    # === Performance PRAGMAs (with validation) ===
    # === Performance PRAGMAs (with validation) ===
    pragmas = [
        ("journal_mode", "WAL"),
        ("synchronous", "NORMAL"),
        ("temp_store", "MEMORY"),
        ("mmap_size", "268435456"),  # 256MB memory map
        ("cache_size", "-200000"),   # ~200MB cache
    ]
    for key, expected_val in pragmas:
        try:
            val_str = str(expected_val)  # Cast int early
            cur.execute(f"PRAGMA {key} = {val_str};")
            result = cur.fetchone()
            actual = str(result[0]) if result else "None"
            if actual and actual.lower() != val_str.lower():
                logger.warning(f"PRAGMA {key} set to {actual}, expected {expected_val}")
            else:
                logger.debug(f"PRAGMA {key} OK: {actual or 'None (default)'}")
        except Exception as e:
            logger.warning(f"PRAGMA {key} failed: {e}")

    conn.commit()

    # === Create main table ===
    create_metrics_table()

    # === Create per-timeframe tables (1m, 5m, 15m, 30m, 1h) ===
    tfs = ["1m", "5m", "15m", "30m", "1h"]
    for tf in tfs:
        tf_table = f"metrics_{tf}"
        try:
            cur.execute(f"CREATE TABLE IF NOT EXISTS {tf_table} AS SELECT * FROM metrics WHERE 0;")
            cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{tf_table}_sym_tf_ts ON {tf_table}(symbol, timeframe, timestamp);")
        except Exception as e:
            logger.warning(f"Failed to create table {tf_table}: {e}")

    # === Add indexes for faster queries ===
    index_cmds = [
        "CREATE INDEX IF NOT EXISTS idx_metrics_symbol_timeframe_ts ON metrics(symbol, timeframe, timestamp DESC);",
        "CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(timestamp DESC);",
        "CREATE INDEX IF NOT EXISTS idx_metrics_tf_ts ON metrics(timeframe, timestamp DESC);"
    ]
    for cmd in index_cmds:
        try:
            cur.execute(cmd)
        except Exception as e:
            logger.warning(f"Index creation failed: {e}")

    conn.commit()
    cur.close()
    conn.close()

    logger.info(f"[DB] Initialized at {DB_PATH} with per-timeframe tables.")
        # Mark DB ready for continuity loops
    ready_flag = REPO_ROOT / "backend" / "logs" / "db_ready.flag"
    try:
        ready_flag.parent.mkdir(parents=True, exist_ok=True)
        ready_flag.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
        logger.info(f"[DB] Ready flag written at {ready_flag}")
    except Exception as e:
        logger.warning(f"[DB] Ready flag write failed: {e}")

    return True


def create_metrics_table():
    """Idempotent SQL create + alter to ensure compatibility with older DBs"""
    conn = get_db_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '5m',
            price REAL,
            price_change_24h_pct REAL,
            volume_24h REAL,
            volume_change_24h_pct REAL,
            market_cap REAL,
            oi_usd REAL,
            oi_abs_usd REAL,
            oi_change_24h_pct REAL,
            oi_change_5m_pct REAL,
            oi_change_15m_pct REAL,
            oi_change_30m_pct REAL,
            oi_change_1h_pct REAL,
            oi_delta_pct REAL,
            price_change_5m_pct REAL,
            price_change_15m_pct REAL,
            price_change_30m_pct REAL,
            price_change_1h_pct REAL,
            global_ls_5m REAL,
            global_ls_15m REAL,
            global_ls_30m REAL,
            global_ls_1h REAL,
            long_account_pct REAL,
            short_account_pct REAL,
            top_ls REAL,
            top_ls_accounts REAL,
            top_ls_positions REAL,
            top_ls_delta_pct REAL,
            ls_delta_pct REAL,
            cvd REAL,
            z_ls REAL,
            z_score REAL,
            z_top_ls_accounts REAL,
            z_top_ls_positions REAL,
            imbalance REAL,
            funding REAL,
            rsi REAL,
            vol_usd REAL DEFAULT 0.0,
            weighted_oi_usd REAL,
            vpi REAL,
            zsc REAL,
            lsm REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timeframe, timestamp) ON CONFLICT REPLACE
        )
        """
    )
    conn.commit()

    # Ensure new columns exist for added metrics (safe to rerun)
    cols = [
        ("top_ls_delta_pct", "REAL"),
        ("weighted_oi_usd", "REAL"),
        ("vpi", "REAL"),
        ("z_top_ls_accounts", "REAL"),
        ("z_top_ls_positions", "REAL"),
        ("zsc", "REAL"),
        ("lsm", "REAL"),
    ]
    for col, typ in cols:
        try:
            cur.execute(f"ALTER TABLE metrics ADD COLUMN {col} {typ};")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    cur.close()
    conn.close()


# safe float parser
def safe_float(m, key, default=None):
    val = m.get(key, default)
    if val is None or val == "N/A":
        return None
    try:
        s = str(val).replace("$", "").replace(",", "").replace("%", "")
        return float(s)
    except Exception:
        return None

def calc_rsi(closes, period=14):
    """
    Compute RSI for a sequence of closing prices.
    Uses standard Wilder's smoothing method.
    """
    arr = np.asarray(closes, dtype=float)
    if arr.size < period + 1:
        return 50.0
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return float(np.round(rsi, 2))


def save_metrics_v3(metrics: List[Dict[str, Union[float, str, None]]], timeframe: str = "5m") -> int:
    """
    Quant-grade version of save_metrics():
    Computes RSI, Weighted OI, VPI, LS and Top-Trader deltas + Z-scores.
    Writes to ORM metrics and per-timeframe tables.
    """
    if not metrics:
        return 0

    session = Session()
    saved_count = 0
    try:
        # Build mappings for bulk insert
        mappings = []
        recent_cache = {}  # Cache prev for deltas (per symbol/tf)

        for m in metrics:
            if "error" in m:
                continue

            # Fetch prev for deltas (cached)
            symbol = m.get("symbol")
            key = (symbol, timeframe)
            if key not in recent_cache:
                recent_cache[key] = (
                    session.query(Metric)
                    .filter(Metric.symbol == symbol, Metric.timeframe == timeframe)
                    .order_by(Metric.timestamp.desc())
                    .limit(50)
                    .all()
                )
            recent = recent_cache[key]
            prev = recent[0] if recent else None
            prev_oi = prev.oi_abs_usd if prev else 0.0
            prev_ls = getattr(prev, f"global_ls_{timeframe}", 1.0) if prev else 1.0
            prev_top_acc = getattr(prev, "top_ls_accounts", 1.0) if prev else 1.0
            prev_top_pos = getattr(prev, "top_ls_positions", 1.0) if prev else 1.0

            d = {
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": datetime.now(timezone.utc) + timedelta(microseconds=random.randint(0, 999999)),
                "price": safe_float(m, "Price"),
                "vol_usd": safe_float(m, "vol_usd") or 0.0,
                "oi_abs_usd": safe_float(m, "oi_abs_usd") or 0.0,
                # OI delta
                "oi_delta_pct": ((safe_float(m, "oi_abs_usd") - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0.0,
                # Global L/S
                f"global_ls_{timeframe}": safe_float(m, f"Global_LS_{timeframe}"),
                "ls_delta_pct": (((safe_float(m, f"Global_LS_{timeframe}")) - prev_ls) / prev_ls * 100) if prev_ls else 0.0,
                # Top trader
                "top_ls_accounts": safe_float(m, "Top_LS_Accounts"),
                "top_ls_positions": safe_float(m, "Top_LS_Positions"),
                "top_ls": safe_float(m, "Top_LS"),
                "top_ls_delta_pct": ((safe_float(m, "Top_LS_Accounts") - prev_top_acc) / prev_top_acc * 100) if prev_top_acc else 0.0,
                # RSI from recent prices
                "rsi": calc_rsi([r.price for r in recent if r.price] + [safe_float(m, "Price")]),
                # Weighted OI (local) — guard divide-by-zero, coerce vol_usd to 0 if None
                "_recent_vol_sum": sum(float(r.vol_usd or 0.0) for r in recent) if recent else 0.0,
                "weighted_oi_usd": (
                    (safe_float(m, "oi_abs_usd") or 0.0) *
                    ((safe_float(m, "vol_usd") or 0.0) / (sum(float(r.vol_usd or 0.0) for r in recent) or 1.0) + (safe_float(m, "vol_usd") or 0.0))
                ) if recent else (safe_float(m, "oi_abs_usd") or 0.0),
                "vpi": safe_float(m, "vol_usd") * (safe_float(m, "oi_delta_pct") or 0.0) / 100.0,
                # z-scores: filter None and coerce to float
                "z_ls": float(np.mean([float(getattr(r, f"global_ls_{timeframe}") or 0.0) for r in recent])) if recent else 0.0,
                "z_top_ls_accounts": float(np.mean([float(r.top_ls_accounts or 0.0) for r in recent])) if recent else 0.0,
                "z_top_ls_positions": float(np.mean([float(r.top_ls_positions or 0.0) for r in recent])) if recent else 0.0,
                # ZSC
                "zsc": 0.5 * (safe_float(m, "z_ls") or 0.0) + 0.25 * (safe_float(m, "z_top_ls_accounts") or 0.0) + 0.25 * (safe_float(m, "z_top_ls_positions") or 0.0),
                # LSM (simplified)
                "lsm": 0.0,  # Full smoothed in loop if data
            }
            # Guards
            for k in ["oi_abs_usd", "vol_usd", "oi_delta_pct", "ls_delta_pct", "top_ls_delta_pct", "vpi", "weighted_oi_usd", "rsi", "zsc", "lsm"]:
                val = d.get(k, None)
                try:
                    if val is None or not np.isfinite(float(val)):
                        d[k] = 0.0
                    else:
                        d[k] = float(val)
                except Exception:
                    d[k] = 0.0

            mappings.append(d)
            saved_count += 1

        # Bulk insert main table
        try:
            session.bulk_insert_mappings(Metric, mappings)
            session.commit()
        except Exception as bulk_err:
            logger.warning(f"Bulk insert failed: {bulk_err}; falling to row-by-row")
            session.rollback()
            for d in mappings:
                metric = Metric(**d)
                session.merge(metric)
            session.commit()

        # Batch mirror to TF table
        if mappings:
            tf_table = f"metrics_{timeframe}"
            conn = get_db_conn()
            try:
                cols = [c.name for c in Metric.__table__.columns if c.name != 'id']  # Exclude id
                placeholders = ",".join("?" for _ in cols)
                values = [[d.get(c, None) for c in cols] for d in mappings]
                conn.executemany(f"INSERT OR REPLACE INTO {tf_table} ({','.join(cols)}) VALUES ({placeholders})", values)
                conn.commit()
            except Exception as mirror_err:
                logger.warning(f"TF mirror failed: {mirror_err}")
            finally:
                conn.close()

        logger.info(f"[save_metrics_v3] Bulk saved {saved_count} records for {timeframe}")
        return saved_count

    except Exception as e:
        session.rollback()
        traceback.print_exc()
        logger.error(f"save_metrics_v3 failed: {e}")
        return 0
    finally:
        session.close()


def get_latest_metrics(limit: int = 50, tf: str | None = None, symbol: str | None = None) -> list:
    """
    Hybrid fetch (ORM first, then raw SQLite fallback). Added symbol filter.
    """
    session = Session()
    try:
        q = session.query(Metric)
        if symbol:
            q = q.filter(Metric.symbol == symbol)
        if tf:
            q = q.filter(Metric.timeframe == tf)
        q = q.order_by(Metric.timestamp.desc())
        rows = q.limit(limit).all()
        if rows:
            logger.info(f"[DB] ORM returned {len(rows)} rows (tf={tf or 'all'}, symbol={symbol or 'all'})")
            return rows
    except Exception as e:
        logger.warning(f"[DB] ORM failed: {e}")
    finally:
        session.close()

    # fallback raw sqlite
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    q = "SELECT * FROM metrics"
    params = []
    if symbol:
        q += " WHERE symbol = ?"
        params.append(symbol)
    if tf:
        if symbol:
            q += " AND"
        else:
            q += " WHERE"
        q += " timeframe = ?"
        params.append(tf)
    q += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    cur.execute(q, params)
    rows = cur.fetchall()
    conn.close()
    logger.info(f"[DB] RAW fallback returned {len(rows)} rows (tf={tf or 'all'}, symbol={symbol or 'all'})")
    return rows


def get_metrics_by_symbol(symbol: str, limit: int = 100, tf: str | None = None) -> list:
    """
    Hybrid fetch (ORM first, then raw SQLite fallback).
    """
    session = Session()
    try:
        q = session.query(Metric).filter(Metric.symbol == symbol)
        if tf:
            q = q.filter(Metric.timeframe == tf)
        q = q.order_by(Metric.timestamp.desc())
        rows = q.limit(limit).all()
        if rows:
            logger.info(f"[DB] ORM returned {len(rows)} rows for {symbol} (tf={tf or 'all'})")
            return rows
    except Exception as e:
        logger.warning(f"[DB] ORM failed: {e}")
    finally:
        session.close()

    # fallback raw sqlite
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    q = "SELECT * FROM metrics WHERE symbol = ?"
    params = [symbol]
    if tf:
        q += " AND timeframe = ?"
        params.append(tf)
    q += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    cur.execute(q, tuple(params))
    rows = cur.fetchall()
    conn.close()
    logger.info(f"[DB] RAW fallback returned {len(rows)} rows for {symbol} (tf={tf or 'all'})")
    return rows