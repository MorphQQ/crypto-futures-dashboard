import sqlite3
con = sqlite3.connect('futures.db')  # Root path fix—no subdir
print('15m rows:', con.execute("SELECT COUNT(*) FROM metrics WHERE timeframe='15m'").fetchone()[0])
print('5m rows:', con.execute("SELECT COUNT(*) FROM metrics WHERE timeframe='5m'").fetchone()[0])
print('Total rows:', con.execute("SELECT COUNT(*) FROM metrics").fetchone()[0])
print('Sample Z (top 3 tf=5m):', con.execute("SELECT symbol, z_ls FROM metrics WHERE timeframe='5m' LIMIT 3").fetchall())
con.close()