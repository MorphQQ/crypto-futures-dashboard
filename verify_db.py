# backend/verify_db.py
import sqlite3
import os

# Backend root (dirname(__file__) = backend)
backend_root = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(backend_root, 'config', 'futures.db')
print(f'DB path: {db_path}')

# Create dir/file if missing
os.makedirs(os.path.dirname(db_path), exist_ok=True)
if not os.path.exists(db_path):
    print('DB file missing—creating empty.')
    open(db_path, 'w').close()

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Count
cursor.execute("SELECT COUNT(*) FROM metrics")
count = cursor.fetchone()[0]
print(f'DB count: {count}')

# Sample rows (top 5 recent)
cursor.execute("SELECT symbol, oi_abs_usd, global_ls_5m, top_ls_accounts, oi_delta_pct FROM metrics ORDER BY timestamp DESC LIMIT 5")
print('Sample rows:')
for row in cursor.fetchall():
    print(f'{row[0]}|{row[1]}|{row[2]}|{row[3]}|{row[4]}')

# Table info (columns)
cursor.execute("PRAGMA table_info(metrics)")
print('\nTable columns:')
for col in cursor.fetchall():
    print(f'{col[1]} ({col[2]})')  # name (type)

conn.close()