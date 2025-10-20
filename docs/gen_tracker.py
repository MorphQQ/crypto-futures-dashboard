# Run: PS: cd docs ; python gen_tracker.py (Appends KPI row w/ git diff; uses '5m' data)  
import sqlite3  
import pandas as pd  
import subprocess  # New: Git diff tease
import os
from datetime import datetime

con = sqlite3.connect(r'E:\Trading\crypto-futures-dashboard\backend\src\futuresboard\futures.db')
# Auto-fix NULL tf  
con.execute("UPDATE metrics SET timeframe = '5m' WHERE timeframe IS NULL")  
con.commit()  
try:  
  # Try '15m' first, fallback to '5m' or total  
  df = pd.read_sql("SELECT AVG(z_score) as avg_z, COUNT(*) as rows FROM metrics WHERE timeframe='15m'", con)  
  if df['rows'][0] == 0:  
    df = pd.read_sql("SELECT AVG(z_score) as avg_z, COUNT(*) as rows FROM metrics WHERE timeframe='5m'", con)  
  # Optim: Git diff append (files_changed env or default 0)
  files_changed = int(os.getenv('FILES_CHANGED', '0'))
  ts = datetime.now().strftime("%Y-%m-%d %H:%M")
  row = f"| P3 | 25% | Framework v1.3 sync ({files_changed} files, {ts}) |\n"  # Tease append (edit phase/pct)
  with open("quant_progress_tracker.md", "a", encoding="utf-8") as f:  # New: >> MD vs print
      f.write(row)
  print(f"Appended KPI row: {row.strip()}")
  print("| Avg Z-Score ('5m') | DB Rows ('5m') |")  
  print("|---------------------|----------------|")  
  print(f"| {df['avg_z'][0]:.2f} | {df['rows'][0]} |")  
except sqlite3.OperationalError as e:  
  if 'z_score' in str(e):  
    print("## DB Note: z_score missing – Run alter_db.py + re-seed")  
    df = pd.read_sql("SELECT AVG(global_ls) as avg_ls, COUNT(*) as rows FROM metrics", con)  
    row = f"| P3 | 25% | Framework v1.3 sync (z_score pending, {ts}) |\n"
    with open("quant_progress_tracker.md", "a", encoding="utf-8") as f:
        f.write(row)
    print(f"Appended fallback row: {row.strip()}")
    print("| Avg Global LS (Total) | DB Rows (Total) |")  
    print("|------------------------|-----------------|")  
    print(f"| {df['avg_ls'][0]:.2f} | {df['rows'][0]} |")  
  else:  
    raise e  
con.close()  

# Optim Tease: Z-roll plot (matplotlib; save png embed) – Comment if no pip
# try:
#     import matplotlib.pyplot as plt
#     df_plot = pd.read_sql("SELECT z_score, timestamp FROM metrics ORDER BY timestamp DESC LIMIT 100", con)
#     df_plot['timestamp'] = pd.to_datetime(df_plot['timestamp'])
#     df_plot.set_index('timestamp', inplace=True)
#     df_plot['z_roll'] = df_plot['z_score'].rolling(20).mean()
#     plt.plot(df_plot['z_roll'])
#     plt.title('Z-Roll Mean Trend (Last 100)')
#     plt.savefig('z_trend.png', dpi=100, bbox_inches='tight')
#     print("Z-trend plot saved: z_trend.png (embed in progress_tracker.md)")
# except ImportError:
#     print("Matplotlib missing: pip install matplotlib (backend/reqs.txt)")