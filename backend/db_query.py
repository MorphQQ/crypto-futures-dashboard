# db_query.py v1.3 – Safe Full-Path, Schema Peek + Finite Averages
import os
import sqlite3
import numpy as np

# --- CONFIG ---
DB_PATH = os.path.abspath("backend/src/futuresboard/futures.db")  # Adjust to your actual DB path
print(f"[Info] Using database: {DB_PATH}")

# --- CONNECTION ---
if not os.path.exists(DB_PATH):
    print("[Error] Database not found. Check path.")
    exit(1)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# --- SCHEMA INSPECT ---
cur.execute("PRAGMA table_info(metrics);")
cols = [row[1] for row in cur.fetchall() if any(k in row[1] for k in [
    'z_ls', 'ls_delta_pct', 'rsi', 'imbalance', 'funding', 'market_cap'
])]
print("[Schema] Columns found:", cols)

# --- SAMPLE DATA ---
query = """
SELECT symbol, z_ls, ls_delta_pct, rsi, imbalance, funding, market_cap
FROM metrics
WHERE timeframe = '5m'
LIMIT 3;
"""
cur.execute(query)
rows = cur.fetchall()
if rows:
    print("[Sample] First 3 rows (5m):", rows)
else:
    print("[Sample] No rows found for timeframe='5m'")

# --- STATS: AVERAGE Z SCORE ---
cur.execute("""
SELECT z_ls FROM metrics
WHERE timeframe = '5m'
AND z_ls IS NOT NULL
AND z_ls BETWEEN -9.99 AND 9.99;
""")
z_values = [r[0] for r in cur.fetchall()]
if z_values:
    avg_z = np.mean(z_values)
    std_z = np.std(z_values)
    print(f"[Stats] Avg Z (finite ±9.99): {avg_z:.2f} | Std Dev: {std_z:.2f} | Count: {len(z_values)}")
else:
    print("[Stats] No valid Z values found.")

# --- CLEANUP ---
conn.close()
print("[Done] Query complete.")
