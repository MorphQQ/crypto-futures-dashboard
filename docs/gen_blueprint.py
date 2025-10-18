# Run: PS: cd docs ; python gen_blueprint.py >> quant_blueprint.md (Append KPIs)
# Fix: Add "pandas numpy" to backend/requirements.txt; pip install -r backend/requirements.txt
import sqlite3
import pandas as pd
import numpy as np
con = sqlite3.connect('../backend/config/futures.db')
df = pd.read_sql("SELECT oi_abs_usd, vol_usd FROM metrics LIMIT 20", con)  # Tease weighted
if len(df) > 0:
    weights = df['vol_usd'] / df['vol_usd'].sum()
    w_oi = np.average(df['oi_abs_usd'], weights=weights)
    print(f"## Auto-KPI Update (Weighted OI Tease)\n| Weighted OI | Current |\n|-------------|---------|\n| ${w_oi/1e9:.2f}B | Avg 20 pairs |")
# Test: code_execution → "| Weighted OI | $6.50B |"