import sqlite3

DB_PATH = "backend/src/futuresboard/futures.db"

conn = sqlite3.connect(DB_PATH)
tables = [
    r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
]
print("\n=== TABLES FOUND ===")
for t in tables:
    print(t)
conn.close()
