import sqlite3

DB_PATH = "backend/src/futuresboard/futures.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

print("\n=== SAMPLE DATA BY SYMBOL & TIMEFRAME ===")
rows = conn.execute("""
    SELECT symbol, timeframe, COUNT(*) as count
    FROM metrics
    GROUP BY symbol, timeframe
    ORDER BY count DESC
    LIMIT 20
""").fetchall()

if not rows:
    print("(No data found in metrics table!)")
else:
    for r in rows:
        print(f"{r['symbol']:<12} {r['timeframe']:<6} rows={r['count']}")

print("\n=== MOST RECENT RECORD (latest timestamp) ===")
sample = conn.execute("""
    SELECT *
    FROM metrics
    ORDER BY timestamp DESC
    LIMIT 1
""").fetchone()

if sample:
    for k in sample.keys():
        print(f"{k:<25} {sample[k]}")
else:
    print("(No sample row found)")

conn.close()
