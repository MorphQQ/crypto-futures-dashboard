# backend/src/futuresboard/quant_engine.py
"""
Quant Engine – Tier 2 Intelligent Metrics Pipeline
Computes rolling OI Z-score, LS Δ%, imbalance %, funding bias, confluence score.
"""

import sqlite3, numpy as np, pandas as pd, datetime

DB_PATH = "backend/src/futuresboard/futures.db"

def compute_quant_metrics(limit: int = 200):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT symbol, timeframe, oi_usd,
               long_account_pct, short_account_pct,
               funding, ls_delta_pct, imbalance, timestamp
        FROM metrics ORDER BY timestamp DESC LIMIT ?
    """, conn, params=(limit,))
    conn.close()
    if df.empty:
        return []

    # Ensure all numeric columns are floats (not objects)
    num_cols = ["oi_usd","long_account_pct","short_account_pct",
                "funding","ls_delta_pct","imbalance"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    # --- OI Z-Score ---
    df["oi_z"] = (df["oi_usd"] - df["oi_usd"].mean()) / (df["oi_usd"].std(ddof=0) or 1)

    # --- LS Delta / Imbalance ---
    if "ls_delta_pct" not in df.columns or df["ls_delta_pct"].isna().all():
        df["ls_delta_pct"] = (df["long_account_pct"] - df["short_account_pct"])
    if "imbalance" not in df.columns or df["imbalance"].isna().all():
        df["imbalance"] = (df["long_account_pct"] / (df["short_account_pct"] + 1e-6)) * 100

    # --- Funding Bias ---
    df["funding_bias"] = pd.to_numeric(df["funding"], errors="coerce").fillna(0.0) * 10000

    # --- Confluence Score ---
    oi_z_np = df["oi_z"].to_numpy(dtype=float)
    ls_np = df["ls_delta_pct"].to_numpy(dtype=float)
    imb_np = df["imbalance"].to_numpy(dtype=float)
    fb_np = df["funding_bias"].to_numpy(dtype=float)

    df["confluence_score"] = (
        np.tanh(np.abs(oi_z_np) / 2)
        + np.tanh(np.abs(ls_np) / 50)
        + np.tanh(np.abs(imb_np) / 200)
        + np.tanh(np.abs(fb_np) / 5)
    ) / 4.0

    # --- Bias Detection ---
    df["bias"] = np.where(df["confluence_score"] > 0.66, "BULL", "BEAR")
    df["updated_at"] = datetime.datetime.utcnow().isoformat()

    return df[[
        "symbol","timeframe","oi_z","ls_delta_pct",
        "imbalance","funding","confluence_score",
        "bias","updated_at"
    ]].to_dict(orient="records")


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
