import sqlite3

DB_PATH = "backend/src/futuresboard/futures.db"

conn = sqlite3.connect(DB_PATH)
cursor = conn.execute("PRAGMA table_info(metrics)")
print("\n=== METRICS TABLE COLUMNS ===")
for cid, name, col_type, *_ in cursor.fetchall():
    print(f"{cid:02d}  {name:<25} {col_type}")
conn.close()
