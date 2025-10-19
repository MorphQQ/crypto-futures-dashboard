# db_query.py: + AVG Z mean tf=5m
import sqlite3

conn = sqlite3.connect('futures.db')
cur = conn.cursor()

# Cols
cur.execute('PRAGMA table_info(metrics);')
cols = [row[1] for row in cur.fetchall() if any(k in row[1] for k in ['z_ls','ls_delta_pct','rsi','imbalance','funding','market_cap'])]
print('Cols OK:', cols)

# Sample finite LIMIT 3
cur.execute("SELECT symbol, z_ls, ls_delta_pct, rsi, imbalance, funding, market_cap FROM metrics WHERE timeframe = '5m' LIMIT 3;")
rows = cur.fetchall()
print('Sample finite (Z/rsi/imb/fund/mcap):', rows)

# AVG Z mean (finite ±9.99; hist=50 for std>0)
cur.execute("SELECT AVG(z_ls) FROM metrics WHERE timeframe = '5m';")
avg_z = cur.fetchone()[0] or 0.0
print('Avg Z finite:', round(avg_z, 2))

conn.close()