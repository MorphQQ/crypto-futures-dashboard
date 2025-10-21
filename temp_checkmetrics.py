import sqlite3
from collections import Counter

DB_PATH = "backend/src/futuresboard/futures.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT symbol, timeframe, COUNT(*) as count FROM metrics GROUP BY symbol, timeframe ORDER BY count DESC LIMIT 20").fetchall()
print("\n=== SAMPLE DATA BY SYMBOL & TIMEFRAME ===")
for r in rows:
    print(f"{r['symbol']:<12} {r['timeframe']:<5}  rows={r['count']}")

sample = conn.execute("SELECT * FROM metrics ORDER BY timestamp DESC LIMIT 1").fetchone()
if sample:
    print("\n=== MOST RECENT RECORD ===")
    for k in sample.keys():
        print(f"{k:<25} {sample[k]}")
else:
    print("\n(No sample row found)")
conn.close()
