# Fixed: backend/src/futuresboard/quant_engine.py
# Changes:
# - GPT Patch A: Per-symbol/tf Z-scores with window=100.
# - Query fix: oi_abs_usd AS oi_usd; added long/short pct fetch note.
# - Incremental: WHERE timestamp > last (simplified).

# backend/src/futuresboard/quant_engine.py
"""
Quant Engine – Tier 2 Intelligent Metrics Pipeline
Computes rolling OI Z-score, LS Δ%, imbalance %, funding bias, confluence score.
"""

import os
import pathlib
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # Repo root
DB_PATH = os.getenv("DB_PATH", str(REPO_ROOT / "backend" / "src" / "futuresboard" / "futures.db"))


import sqlite3, numpy as np, pandas as pd, datetime

DB_PATH = "backend/src/futuresboard/futures.db"

def compute_quant_metrics(limit: int = 200, per_symbol_window: int = 100):
    """
    Compute quant metrics per symbol/timeframe using a rolling window.
      - For each symbol/timeframe, take the latest `per_symbol_window` points
      - Compute OI z-score across that symbol's window (ddof=1 preferred)
      - Compute confluence and bias
    Returns list of dict records.
    """
    import sqlite3, numpy as np, pandas as pd, datetime
    conn = sqlite3.connect(DB_PATH)
    last_sync = datetime.datetime.utcnow() - datetime.timedelta(hours=1)  # Or from config
    # fetch latest rows across symbols, but we will group by symbol/timeframe
    df = pd.read_sql_query("""
        SELECT symbol, timeframe, oi_abs_usd AS oi_usd,
               long_account_pct, short_account_pct,
               funding, ls_delta_pct, imbalance, timestamp
        FROM metrics
        WHERE timestamp > ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, conn, params=(last_sync.isoformat(), limit,))
    conn.close()
    if df.empty:
        return []
    # normalize numeric columns
    num_cols = ["oi_usd","long_account_pct","short_account_pct","funding","ls_delta_pct","imbalance"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    # We'll compute per-group rolling stats
    out_rows = []
    grouped = df.sort_values("timestamp").groupby(["symbol","timeframe"], sort=False)
    for (sym, tf), group in grouped:
        # use last per_symbol_window rows for this symbol/tf
        window = group.tail(per_symbol_window)
        if window.shape[0] < 2:
            # not enough data for meaningful z-score; still emit placeholder
            oi_z = 0.0
        else:
            mean = window["oi_usd"].mean()
            std = window["oi_usd"].std(ddof=1) or 1.0
            oi_z = float((window.iloc[-1]["oi_usd"] - mean) / std)
        ls_delta = float(window.iloc[-1]["ls_delta_pct"]) if "ls_delta_pct" in window.columns else 0.0
        imb = float(window.iloc[-1]["imbalance"]) if "imbalance" in window.columns else 0.0
        funding = float(window.iloc[-1]["funding"]) if "funding" in window.columns else 0.0
        # funding bias scale (original used *10000)
        funding_bias = funding * 10000.0
        # confluence score: normalized, keep same form but ensure numeric arrays
        # scale factors tuned to expected ranges — keep existing but operate on scalars
        cs = (
            np.tanh(abs(oi_z) / 2.0)
            + np.tanh(abs(ls_delta) / 50.0)
            + np.tanh(abs(imb) / 200.0)
            + np.tanh(abs(funding_bias) / 5.0)
        ) / 4.0
        bias = "BULL" if cs > 0.66 else "BEAR"
        updated_at = datetime.datetime.utcnow().isoformat()
        out_rows.append({
            "symbol": sym,
            "timeframe": tf,
            "oi_z": oi_z,
            "ls_delta_pct": ls_delta,
            "imbalance": imb,
            "funding": funding,
            "confluence_score": float(np.round(cs, 6)),
            "bias": bias,
            "updated_at": updated_at,
        })
    return out_rows


def update_quant_summary():
    rows = compute_quant_metrics()
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quant_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, timeframe TEXT,
            oi_z REAL, ls_delta_pct REAL, imbalance REAL,
            funding REAL, confluence_score REAL, bias TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()
    cur.executemany("""
        INSERT INTO quant_summary
        (symbol,timeframe,oi_z,ls_delta_pct,imbalance,funding,
         confluence_score,bias,updated_at)
        VALUES (:symbol,:timeframe,:oi_z,:ls_delta_pct,:imbalance,
                :funding,:confluence_score,:bias,:updated_at)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


# === CLI runner ===
if __name__ == "__main__":
    print("[QuantEngine] Running intelligent metrics update...")
    try:
        count = update_quant_summary()
        if count > 0:
            print(f"[QuantEngine] ✅ Inserted {count} new rows into quant_summary.")
        else:
            print("[QuantEngine] ⚠️ No data found or update skipped.")
    except Exception:
        import traceback
        print("[QuantEngine] ❌ Error during update:")
        traceback.print_exc()