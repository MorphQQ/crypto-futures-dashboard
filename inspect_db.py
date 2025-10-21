import sqlite3

conn = sqlite3.connect("backend/src/futuresboard/futures.db")
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

print("\n=== TABLES ===")
for t in tables:
    try:
        c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except Exception as e:
        c = str(e)
    print(f"{t:<25} {c}")
conn.close()
