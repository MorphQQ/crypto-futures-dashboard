from __future__ import annotations

import sqlite3

from flask import current_app
from flask import g


def get_db():
    """Connect to the application's configured database. The connection
    is unique for each request and will be reused if this is called
    again.
    """
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row

    return g.db


def close_db(e=None):
    """If this request connected to the database, close the
    connection.
    """
    db = g.pop("db", None)

    if db is not None:
        db.close()


def query(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def init_app(app):
    """Register database functions with the Flask app. This is called by
    the application factory.
    """
    app.teardown_appcontext(close_db)
    with app.app_context():  # Ensure table exists on startup
        create_metrics_table()


def create_metrics_table():
    """Create metrics table if it doesn't exist, or ALTER for new columns."""
    # Create if missing
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
            oi_change_24h_pct REAL,
            oi_change_5m_pct REAL,
            oi_change_15m_pct REAL,
            oi_change_30m_pct REAL,
            oi_change_1h_pct REAL,
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
            top_ls_positions REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timestamp) ON CONFLICT REPLACE
        )
    """)
    
    # ALTER for new columns if table exists but missing (dev-safe)
    db = get_db()
    cur = db.cursor()
    new_columns = [
        'price', 'price_change_24h_pct', 'volume_24h', 'volume_change_24h_pct', 'market_cap',
        'oi_change_24h_pct', 'oi_change_5m_pct', 'oi_change_15m_pct', 'oi_change_30m_pct', 'oi_change_1h_pct',
        'price_change_5m_pct', 'price_change_15m_pct', 'price_change_30m_pct', 'price_change_1h_pct',
        'global_ls_5m', 'global_ls_15m', 'global_ls_30m', 'global_ls_1h',
        'long_account_pct', 'short_account_pct', 'top_ls_positions'
    ]
    for col in new_columns:
        try:
            cur.execute(f"ALTER TABLE metrics ADD COLUMN {col} REAL")
            print(f"Added column {col} to metrics table")
        except sqlite3.OperationalError:
            # Column already exists
            pass
    db.commit()
    cur.close()


def save_metrics(metrics):
    """Batch save metrics list to DB (returns inserted count)."""
    if not metrics:
        return 0
    db = get_db()
    try:
        cur = db.cursor()
        sql = """
            INSERT OR REPLACE INTO metrics 
            (symbol, price, price_change_24h_pct, volume_24h, volume_change_24h_pct, market_cap,
             oi_usd, oi_change_24h_pct, oi_change_5m_pct, oi_change_15m_pct, oi_change_30m_pct, oi_change_1h_pct,
             price_change_5m_pct, price_change_15m_pct, price_change_30m_pct, price_change_1h_pct,
             global_ls_5m, global_ls_15m, global_ls_30m, global_ls_1h,
             long_account_pct, short_account_pct, top_ls, top_ls_positions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        values = []
        for m in metrics:
            if 'error' in m:
                continue
            # Safe parse function
            def safe_float(key, default=None):
                val = m.get(key, default)
                if val is None or val == 'N/A':
                    return None
                try:
                    # Clean $, ,, %
                    cleaned = str(val).replace('$', '').replace(',', '').replace('%', '')
                    return float(cleaned)
                except (ValueError, TypeError):
                    print(f"Parse error for {key}: {val}")
                    return None
            
            price = safe_float('Price')
            price_change_24h = safe_float('Price_Change_24h_Pct')
            volume_24h = safe_float('Volume_24h')
            volume_change_24h = safe_float('Volume_Change_24h_Pct')
            market_cap = safe_float('Market_Cap')
            oi_usd = safe_float('OI_USD')
            oi_change_24h = safe_float('OI_Change_24h_Pct')
            oi_change_5m = safe_float('OI_Change_5m_Pct')
            oi_change_15m = safe_float('OI_Change_15m_Pct')
            oi_change_30m = safe_float('OI_Change_30m_Pct')
            oi_change_1h = safe_float('OI_Change_1h_Pct')
            price_change_5m = safe_float('Price_Change_5m_Pct')
            price_change_15m = safe_float('Price_Change_15m_Pct')
            price_change_30m = safe_float('Price_Change_30m_Pct')
            price_change_1h = safe_float('Price_Change_1h_Pct')
            global_ls_5m = safe_float('Global_LS_5m')
            global_ls_15m = safe_float('Global_LS_15m')
            global_ls_30m = safe_float('Global_LS_30m')
            global_ls_1h = safe_float('Global_LS_1h')
            long_account = safe_float('Long_Account_Pct')
            short_account = safe_float('Short_Account_Pct')
            top_ls = safe_float('Top_LS')
            top_ls_positions = safe_float('Top_LS_Positions')
            values.append((
                m['symbol'],
                price,
                price_change_24h,
                volume_24h,
                volume_change_24h,
                market_cap,
                oi_usd,
                oi_change_24h,
                oi_change_5m,
                oi_change_15m,
                oi_change_30m,
                oi_change_1h,
                price_change_5m,
                price_change_15m,
                price_change_30m,
                price_change_1h,
                global_ls_5m,
                global_ls_15m,
                global_ls_30m,
                global_ls_1h,
                long_account,
                short_account,
                top_ls,
                top_ls_positions
            ))
        if values:
            cur.executemany(sql, values)
            db.commit()
            print(f"Saved {len(values)} metrics to DB")  # Temp log
            return len(values)
        return 0
    except sqlite3.Error as e:
        current_app.logger.error(f"DB save error: {e}")  # Use logger if app context
        db.rollback()
        return 0

def get_latest_metrics(limit=50):
    """Query recent metrics for frontend/charts (e.g., last N rows)."""
    return query("SELECT * FROM metrics ORDER BY timestamp DESC LIMIT ?", (limit,), one=False)

def get_metrics_by_symbol(symbol, limit=24):
    """Latest for one symbol (e.g., hourly history)."""
    return query("SELECT * FROM metrics WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?", (symbol, limit), one=False)