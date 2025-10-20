# gen_blueprint.py v1.4 (Oct 20, 2025) — Cross-Drive Fix + Dupe Guard
# Run: cd docs ; python gen_blueprint.py
import sqlite3
import pandas as pd
import numpy as np
import re
import tempfile
import os
import shutil
from pathlib import Path
from datetime import datetime

DB_PATH = Path(r"E:\Trading\crypto-futures-dashboard\futures.db")
MD_PATH = Path("quant_blueprint.md")

def clean_old_kpi(content: str) -> str:
    """Zap old '## Auto-KPI' blocks to prevent dupes."""
    pattern = r"## Auto-KPI Update.*?(?=\n## |\Z)"
    return re.sub(pattern, "", content, flags=re.DOTALL)

def safe_write(path: str, data: str):
    """Temp in same dir + shutil.move for cross-drive safe."""
    dir_path = os.path.dirname(path) or "."
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8", suffix=".md", dir=dir_path) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        shutil.move(tmp_path, path)  # Handles cross-device
    except shutil.Error:
        os.replace(tmp_path, path)  # Fallback same-device

def main():
    print(f"[i] DB: {DB_PATH} | MD: {MD_PATH}")
    kpi = ""

    # Conn + query (with guard; col=vol_usd match seed)
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM metrics")
            total_rows = cur.fetchone()[0]
            print(f"[✓] Rows Total: {total_rows}")

            cur.execute("SELECT COUNT(*) FROM metrics WHERE oi_abs_usd > 0 AND vol_usd > 0")
            valid_rows = cur.fetchone()[0]
            print(f"[✓] Valid OI/Vol: {valid_rows}")

            df = pd.read_sql("SELECT oi_abs_usd, vol_usd FROM metrics ORDER BY timestamp DESC LIMIT 20", con)
            print(f"[✓] Query Rows: {len(df)}")
    except Exception as e:
        print(f"[✗] Conn Error: {e}")
        df = pd.DataFrame()

    # KPI gen
    if df.empty:
        kpi = "## Auto-KPI Update (No Data)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | No rows |\n"
    elif df["vol_usd"].fillna(0).sum() == 0:
        kpi = f"## Auto-KPI Update (Zero/NaN Vol)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | Avg {len(df)} pairs |\n"
    else:
        df_clean = df.dropna(subset=["oi_abs_usd", "vol_usd"])
        if df_clean.empty:
            kpi = "## Auto-KPI Update (All NaN)\n| Weighted OI | Current |\n|-------------|---------|\n| $0.00B | No valid rows |\n"
        else:
            weights = df_clean["vol_usd"] / df_clean["vol_usd"].sum()
            w_oi = np.average(df_clean["oi_abs_usd"], weights=weights)
            kpi = f"## Auto-KPI Update (Weighted OI Tease {datetime.now().strftime('%Y-%m-%d %H:%M')})\n| Weighted OI | Current |\n|-------------|---------|\n| ${w_oi/1e9:.2f}B | Avg {len(df_clean)} pairs |\n"

    # Clean + write (no dupe; safe swap)
    try:
        if MD_PATH.exists():
            with open(MD_PATH, "r", encoding="utf-8") as f:
                content = clean_old_kpi(f.read())
        else:
            content = "# Quant Blueprint\n"
        updated = content + "\n" + kpi
        safe_write(MD_PATH, updated)
        print(f"[✓] MD Updated: {w_oi/1e9:.2f if 'w_oi' in locals() else 0:.2f}B | Single KPI")
    except Exception as e:
        print(f"[✗] Write Error: {e}")

    print(kpi)  # Echo

if __name__ == "__main__":
    main()