#### 3. gen_tracker.py (Updated – Copy-Paste to docs/gen_tracker.py; Add pandas to requirements.txt)  
# Run: PS: cd docs ; python gen_tracker.py (Appends KPI table; test code_execution)
# Fix: Add "pandas" to backend/requirements.txt; pip install -r backend/requirements.txt
import sqlite3
import pandas as pd
con = sqlite3.connect('../backend/config/futures.db')
df = pd.read_sql("SELECT AVG(z_score) as avg_z, COUNT(*) as rows FROM metrics WHERE timeframe='15m'", con)
print("| Avg Z-Score | DB Rows (15m) |")
print("|-------------|---------------|")
print(f"| {df['avg_z'][0]:.2f} | {df['rows'][0]} |")
# Extend for more KPIs: e.g., alert_count = pd.read_sql("SELECT COUNT(*) FROM alerts WHERE date > '2025-10-17'", con)