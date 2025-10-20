# Run: PS: cd docs ; python gen_blueprint.py >> quant_blueprint.md (Append VIII KPI w/ weighted)
# Fix: Path src/futuresboard/; Req pandas/np in backend/reqs.txt
import sqlite3
import pandas as pd
import numpy as np

db_path = r'E:\Trading\crypto-futures-dashboard\backend\src\futuresboard\futures.db'
print(f"DB Path: {db_path}")  # Echo for path check

try:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM metrics")  # Total rows peek
    total_rows = cur.fetchone()[0]
    print(f"DB Rows Total: {total_rows}")  # Expect ~17.5k from seed
    
    cur.execute("SELECT COUNT(*) FROM metrics WHERE oi_abs_usd > 0 AND vol_usd > 0")  # Valid data check
    valid_rows = cur.fetchone()[0]
    print(f"DB Valid OI/Vol Rows: {valid_rows}")  # >0?
    
    con.close()  # Temp close after checks
    print("Conn Check: OK")  # Success echo
except Exception as e:
    print(f"Conn Error: {e}")  # e.g., perms/path
    con = None

if con is None:
    kpi = "## Auto-KPI Update (Conn Fail)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | Fix DB path/perms |\n"
else:
    # Re-open for query
    con = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT oi_abs_usd, vol_usd FROM metrics ORDER BY timestamp DESC LIMIT 20", con)  # Recent rows for weighted
    print(f"Query Rows Loaded: {len(df)}")  # 20? or 0?
    con.close()
    
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