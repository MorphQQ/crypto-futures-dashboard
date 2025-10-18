# Run: PS: cd docs ; python gen_tracker.py (Appends KPI; uses '5m' data)  
import sqlite3  
import pandas as pd  
con = sqlite3.connect('../backend/config/futures.db')  
# Auto-fix NULL tf  
con.execute("UPDATE metrics SET timeframe = '5m' WHERE timeframe IS NULL")  
con.commit()  
try:  
  # Try '15m' first, fallback to '5m' or total  
  df = pd.read_sql("SELECT AVG(z_score) as avg_z, COUNT(*) as rows FROM metrics WHERE timeframe='15m'", con)  
  if df['rows'][0] == 0:  
    df = pd.read_sql("SELECT AVG(z_score) as avg_z, COUNT(*) as rows FROM metrics WHERE timeframe='5m'", con)  
  print("| Avg Z-Score ('5m') | DB Rows ('5m') |")  
  print("|---------------------|----------------|")  
  print(f"| {df['avg_z'][0]:.2f} | {df['rows'][0]} |")  
except sqlite3.OperationalError as e:  
  if 'z_score' in str(e):  
    print("## DB Note: z_score missing – Run alter_db.py + re-seed")  
    df = pd.read_sql("SELECT AVG(global_ls) as avg_ls, COUNT(*) as rows FROM metrics", con)  
    print("| Avg Global LS (Total) | DB Rows (Total) |")  
    print("|------------------------|-----------------|")  
    print(f"| {df['avg_ls'][0]:.2f} | {df['rows'][0]} |")  
  else:  
    raise e  
con.close()  