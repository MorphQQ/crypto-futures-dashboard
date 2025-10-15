from __future__ import annotations

import sqlite3
import os
import random  # For timestamp jitter in UNIQUE
import traceback  # For print_exc in save_metrics except
from datetime import datetime, timedelta, timezone  # timedelta for jitter, timezone for utcnow deprecation

from flask import current_app, g
from sqlalchemy import Column, Integer, String, DateTime, Float, create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base  # v1.4 compatible
from .config import Config

import pathlib  # For Path coerce

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
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))  # UTC-aware default (no deprecation)
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
    """Create metrics table if missing, ALTER for new cols (dev-safe)."""
    # Raw SQL create (your existing, with UNIQUE for upsert)
    query("""
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
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
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timestamp) ON CONFLICT REPLACE
        )
    """)
    
    # ALTER for new columns (your existing logic)
    db = get_db()
    cur = db.cursor()
    new_columns = [
        'price', 'price_change_24h_pct', 'volume_24h', 'volume_change_24h_pct', 'market_cap',
        'oi_change_24h_pct', 'oi_change_5m_pct', 'oi_change_15m_pct', 'oi_change_30m_pct', 'oi_change_1h_pct',
        'price_change_5m_pct', 'price_change_15m_pct', 'price_change_30m_pct', 'price_change_1h_pct',
        'global_ls_5m', 'global_ls_15m', 'global_ls_30m', 'global_ls_1h',
        'long_account_pct', 'short_account_pct', 'top_ls', 'top_ls_positions',
        'oi_abs_usd', 'oi_delta_pct', 'top_ls_accounts'
    ]
    for col in new_columns:
        try:
            cur.execute(f"ALTER TABLE metrics ADD COLUMN {col} REAL")
            print(f"Added column {col} to metrics table")
        except sqlite3.OperationalError:
            pass  # Already exists
    db.commit()
    cur.close()

def save_metrics(metrics):
    """Batch save metrics to DB using ORM merge (upserts + pre-calc deltas; fallback raw)."""
    if not metrics:
        return 0
    session = Session()
    try:
        saved_count = 0
        for m in metrics:
            if 'error' in m:
                continue
            # Safe parse (your existing)
            def safe_float(key, default=None):
                val = m.get(key, default)
                if val is None or val == 'N/A':
                    return None
                try:
                    cleaned = str(val).replace('$', '').replace(',', '').replace('%', '')
                    return float(cleaned)
                except (ValueError, TypeError):
                    print(f"Parse error for {key}: {val}")
                    return None

            # Build Metric instance
            metric = Metric(
                symbol = m['symbol'],
                timestamp = datetime.now(timezone.utc) + timedelta(microseconds=random.randint(0,999999)),  # Explicit + jitter for UNIQUE
                price = safe_float('Price'),
                price_change_24h_pct = safe_float('Price_Change_24h_Pct'),
                volume_24h = safe_float('Volume_24h'),
                volume_change_24h_pct = safe_float('Volume_Change_24h_Pct'),
                market_cap = safe_float('Market_Cap'),
                oi_usd = safe_float('OI_USD'),
                oi_abs_usd = safe_float('oi_abs_usd', safe_float('OI_USD')),  # Normalize if missing
                oi_change_24h_pct = safe_float('OI_Change_24h_Pct'),
                oi_change_5m_pct = safe_float('OI_Change_5m_Pct'),
                oi_change_15m_pct = safe_float('OI_Change_15m_Pct'),
                oi_change_30m_pct = safe_float('OI_Change_30m_Pct'),
                oi_change_1h_pct = safe_float('OI_Change_1h_Pct'),
                price_change_5m_pct = safe_float('Price_Change_5m_Pct'),
                price_change_15m_pct = safe_float('Price_Change_15m_Pct'),
                price_change_30m_pct = safe_float('Price_Change_30m_Pct'),
                price_change_1h_pct = safe_float('Price_Change_1h_Pct'),
                global_ls_5m = safe_float('Global_LS_5m'),
                global_ls_15m = safe_float('Global_LS_15m'),
                global_ls_30m = safe_float('Global_LS_30m'),
                global_ls_1h = safe_float('Global_LS_1h'),
                long_account_pct = safe_float('Long_Account_Pct'),
                short_account_pct = safe_float('Short_Account_Pct'),
                top_ls = safe_float('Top_LS'),
                top_ls_accounts = safe_float('Top_LS_Accounts', safe_float('Top_LS')),  # Fallback
                top_ls_positions = safe_float('Top_LS_Positions')
            )

            # Pre-calc oi_delta_pct (roadmap: rolling % from prev)
            prev = session.query(Metric).filter(Metric.symbol == m['symbol']).order_by(Metric.timestamp.desc()).first()
            if prev and prev.oi_abs_usd and metric.oi_abs_usd:
                metric.oi_delta_pct = ((metric.oi_abs_usd - prev.oi_abs_usd) / prev.oi_abs_usd * 100)
                print(f"oi_delta_pct pre-calc for {m['symbol']}: {metric.oi_delta_pct:.2f}%")  # Debug
            else:
                metric.oi_delta_pct = 0.0
                print(f"oi_delta_pct default 0.0 for {m['symbol']} (no prev or oi_abs_usd None)")

            print(f"Merging {m['symbol']}: oi_abs_usd={metric.oi_abs_usd}, global_ls_5m={metric.global_ls_5m}")  # Debug

            session.merge(metric)  # Upsert (roadmap)
            saved_count += 1
        session.commit()
        print(f"Saved {saved_count} metrics to DB - Post-commit count: {session.query(Metric).count()}")  # Debug count
        return saved_count
    except Exception as e:
        print(f"DB save error: {e}")
        traceback.print_exc()  # Full stack
        session.rollback()
        return 0
    finally:
        session.close()  # Safety

def get_latest_metrics(limit=50):
    """Query recent metrics (ORM for frontend/charts)."""
    session = Session()
    try:
        return session.query(Metric).order_by(Metric.timestamp.desc()).limit(limit).all()
    finally:
        session.close()

def get_metrics_by_symbol(symbol, limit=24):
    """Latest for one symbol (ORM)."""
    session = Session()
    try:
        return session.query(Metric).filter(Metric.symbol == symbol).order_by(Metric.timestamp.desc()).limit(limit).all()
    finally:
        session.close()