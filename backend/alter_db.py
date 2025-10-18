import sqlite3  
con = sqlite3.connect('config/futures.db')  
# Add timeframe if missing  
try:  
  con.execute("ALTER TABLE metrics ADD COLUMN timeframe TEXT DEFAULT '5m'")  
  print('timeframe added')  
except sqlite3.OperationalError as e:  
  if 'duplicate column name' not in str(e).lower():  
    raise e  
  print('timeframe already exists')  
# Add z_score if missing (from prior)  
try:  
  con.execute("ALTER TABLE metrics ADD COLUMN z_score REAL DEFAULT 0.0")  
  print('z_score added')  
except sqlite3.OperationalError as e:  
  if 'duplicate column name' not in str(e).lower():  
    raise e  
  print('z_score already exists')  
# Add vol_usd if missing (for P3 weighted OI tease)
try:
  con.execute("ALTER TABLE metrics ADD COLUMN vol_usd REAL DEFAULT 0.0")
  print('vol_usd added')
except sqlite3.OperationalError as e:
  if 'duplicate column name' not in str(e).lower():
    raise e
  print('vol_usd already exists')
# Update NULL tf to '5m'  
con.execute("UPDATE metrics SET timeframe = '5m' WHERE timeframe IS NULL OR timeframe = ''")  
con.commit()  
print(f'NULL tf updated – Rows affected: {con.total_changes}')  
con.close()  