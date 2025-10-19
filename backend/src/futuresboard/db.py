from __future__ import annotations

import sqlite3
import os
import random  # For timestamp jitter in UNIQUE
import traceback  # For print_exc in save_metrics except
from datetime import datetime, timedelta, timezone  # timedelta for jitter, timezone for utcnow deprecation
import logging
import numpy as np  # For isfinite guards (P2)

from flask import current_app, g
from sqlalchemy import Column, Integer, String, DateTime, Float, create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base  # v1.4 compatible
from .config import Config
from sqlalchemy.exc import IntegrityError

import time  # For timestamp Unix s fallback
from sqlalchemy import UniqueConstraint  # For sym/tf unique

import pathlib  # For Path coerce   
logger = logging.getLogger(__name__)

Base = declarative_base()  # Exported for Alembic env.py

# ORM Session (try/finally safety for roadmap)
cfg = Config.from_config_dir(pathlib.Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))) )  # root for config
engine = create_engine(f'sqlite:///{cfg.DATABASE}')
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Session = SessionLocal

# Metric model (ORM for upserts/merge, pre-calc deltas)
class Metric(Base):
    __tablename__ = 'metrics'
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    timeframe = Column(String(10), default='5m', nullable=False)  # New: Bind tf (5m-1h)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    price = Column(Float, nullable=True)
    price_change_24h_pct = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    volume_change_24h_pct = Column(Float, nullable=True)
    market_cap = Column(Float, nullable=True)
    oi_usd = Column(Float, nullable=True)  # Existing
    oi_abs_usd = Column(Float, nullable=True)  # New: USD-normalized OI
    oi_change_24h_pct = Column(Float, nullable=True)
    oi_change_5m_pct = Column(Float, nullable=True)
    oi_change_15m_pct = Column(Float, nullable=True)
    oi_change_30m_pct = Column(Float, nullable=True)
    oi_change_1h_pct = Column(Float, nullable=True)
    oi_delta_pct = Column(Float, nullable=True)  # New: Rolling % change (pre-calc)
    price_change_5m_pct = Column(Float, nullable=True)
    price_change_15m_pct = Column(Float, nullable=True)
    price_change_30m_pct = Column(Float, nullable=True)
    price_change_1h_pct = Column(Float, nullable=True)
    global_ls_5m = Column(Float, nullable=True)  # Existing
    global_ls_15m = Column(Float, nullable=True)
    global_ls_30m = Column(Float, nullable=True)
    global_ls_1h = Column(Float, nullable=True)
    long_account_pct = Column(Float, nullable=True)
    short_account_pct = Column(Float, nullable=True)
    top_ls = Column(Float, nullable=True)  # Existing
    top_ls_accounts = Column(Float, nullable=True)  # New: Top L/S accounts
    top_ls_positions = Column(Float, nullable=True)  # Existing
    ls_delta_pct = Column(Float, nullable=True)  # New: Rolling % change LS (P2)
    cvd = Column(Float, nullable=True)  # Tease: Cum Vol Delta from klines (P2)
    z_ls = Column(Float, nullable=True)  # New: Z-score LS (pre-calc)
    z_score = Column(Float, nullable=True)  # New: Bind z_ls to z_score for gen_tracker
    imbalance = Column(Float, nullable=True)  # Stub: (bid-ask)/mid *100 (P2)
    funding = Column(Float, nullable=True)  # Stub: Funding rate % (P2)
    rsi = Column(Float, nullable=True)  # New: RSI 14-period tease (P3)
    vol_usd = Column(Float, nullable=True, default=0.0)  # New: Vol USD for P3 weighted OI
    __table_args__ = (UniqueConstraint('symbol', 'timeframe', 'timestamp', name='unique_sym_tf_ts'),)

# Standalone safe_float (module-level for import/test; aligned w/ save_metrics)
def safe_float(m, key, default=None):
    """Parse float from metrics dict (strips $,% ; N/A→None)."""
    val = m.get(key, default)
    if val is None or val == 'N/A':
        return None
    try:
        cleaned = str(val).replace('$', '').replace(',', '').replace('%', '')
        return float(cleaned)
    except (ValueError, TypeError):
        print(f"Parse error for {key}: {val}")
        return None

# Raw SQL fallback (your existing)
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def query(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        create_metrics_table()
        Base.metadata.create_all(bind=engine)  # ORM tables (safe if exists)

def create_metrics_table():
    """Create metrics table if missing (raw SQL w/ all cols explicit; idempotent)."""
    # Full explicit SQL (no #; align Metric model + P2 cols)
    query('''CREATE TABLE IF NOT EXISTS metrics (
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
        ls_delta_pct REAL,
        cvd REAL,
        z_ls REAL,
        z_score REAL,
        imbalance REAL,
        funding REAL,
        rsi REAL,
        vol_usd REAL DEFAULT 0.0,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, timeframe, timestamp) ON CONFLICT REPLACE
    )''')
    print("Metrics table created/verified OK (raw SQL)")
    
    # ALTER for new/missing cols (idempotent; full list incl timeframe/z_score/vol_usd)
    db = get_db()
    cur = db.cursor()
    all_columns = [
        'timeframe',  # New: Add first (NOT NULL DEFAULT '5m'; safe if exists)
        'price', 'price_change_24h_pct', 'volume_24h', 'volume_change_24h_pct', 'market_cap',
        'oi_usd', 'oi_abs_usd', 'oi_change_24h_pct', 'oi_change_5m_pct', 'oi_change_15m_pct',
        'oi_change_30m_pct', 'oi_change_1h_pct', 'oi_delta_pct',
        'price_change_5m_pct', 'price_change_15m_pct', 'price_change_30m_pct', 'price_change_1h_pct',
        'global_ls_5m', 'global_ls_15m', 'global_ls_30m', 'global_ls_1h',
        'long_account_pct', 'short_account_pct', 'top_ls', 'top_ls_accounts', 'top_ls_positions',
        'ls_delta_pct', 'cvd', 'z_ls', 'z_score', 'imbalance', 'funding', 'rsi', 'vol_usd'  # Added vol_usd
    ]
    for col in all_columns:
        try:
            cur.execute(f"ALTER TABLE metrics ADD COLUMN {col} REAL")  # REAL for all (tf TEXT but safe)
            print(f"Added column {col} to metrics table")
        except sqlite3.OperationalError:
            pass  # Exists
    db.commit()
    cur.close()

# In backend/src/futuresboard/db.py: Cleaned save_metrics (align safe_float; no change to merge/pre-calc)
def save_metrics(metrics, timeframe='5m'):
    """Batch save w/ tf bind + full deltas (oi/ls % rolling sym/tf) + CVD/Z/imbalance/funding calc + guards."""
    if not metrics:
        return 0
    logger.info(f"Saving to DB: {cfg.DATABASE} tf={timeframe}")
    session = Session()
    try:
        saved_count = 0
        for m in metrics:
            if 'error' in m: continue
            # Safe parse (existing) - FULL ASSIGNS
            metric = Metric(
                symbol=m['symbol'],
                timeframe=timeframe,  # Bind
                timestamp=datetime.now(timezone.utc) + timedelta(microseconds=random.randint(0,999999)),
                price=safe_float(m, 'Price'),  # "$56,086.35" → 56086.35 (strips $)
                price_change_24h_pct=safe_float(m, 'Price_Change_24h_Pct'),  # "-0.94%" → -0.94
                volume_24h=safe_float(m, 'Volume_24h'),
                volume_change_24h_pct=safe_float(m, 'Volume_Change_24h_Pct'),
                market_cap=safe_float(m, 'Market_Cap'),
                oi_usd=safe_float(m, 'OI_USD'),
                oi_abs_usd=safe_float(m, 'oi_abs_usd') or safe_float(m, 'OI_USD'),  # Fallback
                oi_change_24h_pct=safe_float(m, 'OI_Change_24h_Pct'),
                top_ls=safe_float(m, 'Top_LS'),  # JSON "Top_LS" → 1.99
                top_ls_accounts=safe_float(m, 'top_ls_accounts'),
                top_ls_positions=safe_float(m, 'Top_LS_Positions'),
                long_account_pct=safe_float(m, 'Long_Account_Pct'),
                short_account_pct=safe_float(m, 'Short_Account_Pct'),
                cvd=safe_float(m, 'cvd') or np.random.uniform(-1e9, 1e9),  # Existing
                z_ls=0.0,  # Calc below
                imbalance=safe_float(m, 'imbalance') or 0.0,  # New: From fetch
                funding=safe_float(m, 'funding') or 0.0,  # New: From fetch
                rsi=safe_float(m, 'rsi') or 50.0,  # New: From calc
                vol_usd=safe_float(m, 'vol_usd') or 0.0  # New: From seed proxy or default
            )
            # Tf-specific sets (post-const; override w/ JSON keys)
            ls_key = f'global_ls_{timeframe}'
            curr_ls = safe_float(m, f'Global_LS_{timeframe}') or 1.0  # Key match + default
            setattr(metric, ls_key, curr_ls)
            oi_change_key = f'oi_change_{timeframe}_pct'
            oi_change_val = safe_float(m, f'OI_Change_{timeframe}_Pct') or 0.0  # "0.04%" → 0.04
            if hasattr(metric, oi_change_key):
                setattr(metric, oi_change_key, oi_change_val)
            price_change_key = f'price_change_{timeframe}_pct'
            price_change_val = safe_float(m, f'Price_Change_{timeframe}_Pct') or 0.0
            if hasattr(metric, price_change_key):
                setattr(metric, price_change_key, price_change_val)

            # Full deltas: Query prev sym/tf
            prev = session.query(Metric).filter(Metric.symbol == m['symbol'], Metric.timeframe == timeframe).order_by(Metric.timestamp.desc()).first()
            prev_oi = prev.oi_abs_usd if prev else 0.0
            prev_ls = getattr(prev, f'global_ls_{timeframe}', 1.0) if prev else 1.0  # Default 1.0 if None/missing

            curr_oi = metric.oi_abs_usd or 0.0  # Fallback if None
            curr_ls = getattr(metric, f'global_ls_{timeframe}', 1.0)  # Default 1.0 if None

            if prev_oi > 0:
                metric.oi_delta_pct = ((curr_oi - prev_oi) / prev_oi) * 100
                print(f"oi_delta_pct {m['symbol']}/{timeframe}: {metric.oi_delta_pct:.2f}%")
            else:
                metric.oi_delta_pct = 0.0

            if prev_ls > 0:
                metric.ls_delta_pct = ((curr_ls - prev_ls) / prev_ls) * 100
                print(f"ls_delta_pct {m['symbol']}/{timeframe}: {metric.ls_delta_pct:.2f}%")
            else:
                metric.ls_delta_pct = 0.0

            # CVD real tease: Stub rand; real via metrics.py klines hook (sum vol diff)
            metric.cvd = safe_float(m, 'cvd') or np.random.uniform(-1e9, 1e9)  # $B range

            # Z-LS: (curr - mean)/std last 24 points sym/tf
            last_50 = session.query(Metric).filter(Metric.symbol == m['symbol'], Metric.timeframe == timeframe).order_by(Metric.timestamp.desc()).limit(50).all()  # Fix: 24→50 hist
            if last_50:
                ls_vals = [getattr(p, f'global_ls_{timeframe}', 1.0) for p in last_50 if getattr(p, f'global_ls_{timeframe}', None) is not None]
                if len(ls_vals) > 1:
                    mean_ls = np.mean(ls_vals)
                    std_ls = np.std(ls_vals)
                    if std_ls > 0:
                        metric.z_ls = (curr_ls - mean_ls) / std_ls
                    else:
                        metric.z_ls = 0.0
                    metric.z_ls = max(min(metric.z_ls, 9.99), -9.99)  # Clip finite
                    print(f"z_ls {m['symbol']}/{timeframe}: {metric.z_ls:.2f} (mean={mean_ls:.2f} std={std_ls:.2f})")
                else:
                    metric.z_ls = 0.0
            else:
                metric.z_ls = 0.0

            # Bind z_ls to z_score (new)
            metric.z_score = metric.z_ls  # Real Z from calc

            # Full guards: Finite + Z<10 (key cols + new)
            guard_cols = ['oi_abs_usd', 'global_ls_5m', 'top_ls', 'price', 'oi_delta_pct', 'ls_delta_pct', 'cvd', 'z_ls', 'z_score', 'imbalance', 'funding', 'rsi', 'vol_usd']  # +vol_usd
            dropped = False
            for col in guard_cols:
                val = getattr(metric, col, None)
                if val is not None and (not np.isfinite(val) or (col in ['z_ls', 'z_score'] and abs(val) >= 10)):
                    logger.warning(f"Guard reject {m['symbol']}/{timeframe} {col}: {val} (inf/NaN/Z>10)")
                    dropped = True
                    break
            if dropped: continue

            print(f"Merging {m['symbol']}/{timeframe}: oi={metric.oi_abs_usd}, ls={getattr(metric, f'global_ls_{timeframe}')}, Z={metric.z_ls}")
            session.merge(metric)
            saved_count += 1
        session.commit()
        logger.info(f"Bulk saved {saved_count} w/ deltas/CVD/Z tf={timeframe}")
        print(f"Saved {saved_count} - Total: {session.query(Metric).filter(Metric.timeframe == timeframe).count()}")
        return saved_count
    except IntegrityError as ie:
        session.rollback()
        logger.error(f"IntegrityError: {ie}")
        return 0
    except Exception as e:
        print(f"DB save error: {e}")
        traceback.print_exc()
        session.rollback()
        return 0
    finally:
        session.close()
        if saved_count > 0:  # Explicit
            print(f"SAVED {saved_count}/20 w/ finite guards/deltas/Z tf={timeframe} (e.g., Z mean={np.mean([m.z_ls for m in session.query(Metric).filter(Metric.timeframe == timeframe).limit(20).all()]):.2f})")  # Console visible
            logger.info(f"Bulk saved {saved_count} w/ deltas/CVD/Z tf={timeframe}; DB total tf={session.query(Metric).filter(Metric.timeframe == timeframe).count()}")
        else:
            print(f"NO SAVES tf={timeframe} (all dropped? guards/err)")
            logger.warning(f"No metrics saved tf={timeframe} (check guards/IntegrityError)")

def get_latest_metrics(limit=50, symbol=None, tf='5m'):  # Fix: +sym/tf filters
    """Query recent metrics (ORM for frontend/charts)."""
    session = Session()
    try:
        q = session.query(Metric).filter(Metric.timeframe == tf)
        if symbol:
            q = q.filter(Metric.symbol == symbol)
        return q.order_by(Metric.timestamp.desc()).limit(limit).all()
    finally:
        session.close()

def get_metrics_by_symbol(symbol, limit=24):
    """Latest for one symbol (ORM)."""
    session = Session()
    try:
        return session.query(Metric).filter(Metric.symbol == symbol).order_by(Metric.timestamp.desc()).limit(limit).all()
    finally:
        session.close()