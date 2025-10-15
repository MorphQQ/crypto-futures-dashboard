# backend/add_columns.py
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), 'config', 'futures.db')
print(f'Adding Phase 1 columns to {db_path}')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

new_columns = [
    'oi_abs_usd', 'oi_delta_pct', 'top_ls_accounts',  # Phase 1 core
    'global_ls_5m', 'global_ls_15m', 'global_ls_30m', 'global_ls_1h',  # L/S timeframes
    'long_account_pct', 'short_account_pct'  # L/S accounts
]

for col in new_columns:
    try:
        cursor.execute(f"ALTER TABLE metrics ADD COLUMN {col} REAL")
        print(f'Added column {col} to metrics table')
    except sqlite3.OperationalError as e:
        if 'duplicate column name' in str(e):
            print(f'Column {col} already exists (skipping)')
        else:
            print(f'Error adding {col}: {e}')

conn.commit()
cursor.close()
print('ALTER complete—run verify_db.py to check.')