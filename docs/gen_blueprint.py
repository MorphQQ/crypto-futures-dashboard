# Run: PS: cd docs ; python gen_blueprint.py >> quant_blueprint.md (Append KPIs)
# Fix: Add "pandas numpy" to backend/requirements.txt; pip install -r backend/requirements.txt
import sqlite3
import pandas as pd
import numpy as np
con = sqlite3.connect('../backend/config/futures.db')
df = pd.read_sql("SELECT oi_abs_usd, vol_usd FROM metrics LIMIT 20", con)  # Tease weighted
if len(df) == 0:
    print("## Auto-KPI Update (No Data)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | No rows |")
elif pd.isna(df['vol_usd']).all() or df['vol_usd'].sum() == 0:
    print("## Auto-KPI Update (Zero/NaN Vol)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | Avg {} pairs (default vol)".format(len(df)))
else:
    # Drop NaN rows for clean weights
    df_clean = df.dropna(subset=['oi_abs_usd', 'vol_usd'])
    if len(df_clean) == 0:
        print("## Auto-KPI Update (All NaN)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | No valid rows |")
    else:
        weights = df_clean['vol_usd'] / df_clean['vol_usd'].sum()
        w_oi = np.average(df_clean['oi_abs_usd'], weights=weights)
        print("## Auto-KPI Update (Weighted OI Tease)\n| Weighted OI | Current |\n|-------------|---------|\n| ${:.2f}B | Avg {} pairs |".format(w_oi/1e9, len(df_clean)))
con.close()