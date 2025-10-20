# Run: PS: cd docs ; python gen_blueprint.py >> quant_blueprint.md (Append VIII KPI w/ weighted)
# Fix: Path src/futuresboard/; Req pandas/np in backend/reqs.txt
import sqlite3
import pandas as pd
import numpy as np
con = sqlite3.connect('../../backend/src/futuresboard/futures.db')  # Fix: Screenshot path
df = pd.read_sql("SELECT oi_abs_usd, vol_usd FROM metrics ORDER BY timestamp DESC LIMIT 20", con)  # Recent rows for weighted
if len(df) == 0:
    kpi = "## Auto-KPI Update (No Data)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | No rows |\n"
elif pd.isna(df['vol_usd']).all() or df['vol_usd'].sum() == 0:
    kpi = "## Auto-KPI Update (Zero/NaN Vol)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | Avg {} pairs (default vol)\n".format(len(df))
else:
    # Drop NaN rows for clean weights
    df_clean = df.dropna(subset=['oi_abs_usd', 'vol_usd'])
    if len(df_clean) == 0:
        kpi = "## Auto-KPI Update (All NaN)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | No valid rows |\n"
    else:
        weights = df_clean['vol_usd'] / df_clean['vol_usd'].sum()
        w_oi = np.average(df_clean['oi_abs_usd'], weights=weights)
        kpi = "## Auto-KPI Update (Weighted OI Tease)\n| Weighted OI | Current |\n|-------------|---------|\n| ${:.2f}B | Avg {} pairs |\n".format(w_oi/1e9, len(df_clean))
# Optim: >> blueprint.md VIII vs print
with open("quant_blueprint.md", "a", encoding="utf-8") as f:
    f.write(kpi)
print(kpi)
con.close()