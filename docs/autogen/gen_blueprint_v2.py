# gen_blueprint_v2.py – DR-P2 Integration (Context-Aware, AutoDocs Ready)
# Usage: cd docs/autogen ; python gen_blueprint_v2.py
import sqlite3
import pandas as pd
import numpy as np
import json
import os
import re
import tempfile
import shutil
from datetime import datetime
from pathlib import Path

CONTEXT_PATH = Path(r"E:\Trading\crypto-futures-dashboard\docs\project_context_v3.json")
OUTPUT_PATH = Path(r"E:\Trading\crypto-futures-dashboard\docs\quant_blueprint_synced.md")

def clean_old_kpi(content: str) -> str:
    pattern = r"## Auto-KPI Update.*?(?=\n## |\Z)"
    return re.sub(pattern, "", content, flags=re.DOTALL)

def safe_write(path: Path, data: str):
    dir_path = path.parent
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8", suffix=".md", dir=dir_path) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    shutil.move(tmp_path, path)

def load_context():
    try:
        with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[✗] Context load failed: {e}")
        return {}

def main():
    ctx = load_context()
    db_path = ctx.get("database_path", "backend/src/futuresboard/futures.db")
    phase = ctx.get("phase", "Unknown")
    uptime = ctx.get("uptime_pct", 0)

    db_abs = Path(r"E:\Trading\crypto-futures-dashboard") / Path(db_path)
    print(f"[i] DB: {db_abs} | OUT: {OUTPUT_PATH}")

    try:
        with sqlite3.connect(db_abs) as con:
            df = pd.read_sql("SELECT oi_abs_usd, vol_usd FROM metrics ORDER BY timestamp DESC LIMIT 50", con)
    except Exception as e:
        print(f"[✗] DB error: {e}")
        df = pd.DataFrame()

    if df.empty or df['vol_usd'].fillna(0).sum() == 0:
        weighted_oi = 0.0
        status = "No valid OI/Vol data"
    else:
        weights = df['vol_usd'] / df['vol_usd'].sum()
        weighted_oi = np.average(df['oi_abs_usd'], weights=weights)
        status = f"Weighted OI from {len(df)} pairs"

    kpi_md = (
        "## Auto-KPI Update (" + datetime.now().strftime("%Y-%m-%d %H:%M") + ")\n"
        "| Metric | Value |\n"
        "|---------|-------|\n"
        f"| **Weighted OI (USD)** | \\${weighted_oi/1e9:.2f}B |\n"
        f"| **Phase** | {phase} |\n"
        f"| **Uptime** | {uptime:.1f}% |\n"
        f"| **Status** | {status} |\n"
    )

    try:
        if OUTPUT_PATH.exists():
            content = clean_old_kpi(OUTPUT_PATH.read_text(encoding="utf-8"))
        else:
            content = "# Quant Blueprint (Synced)\n"
        updated = content.strip() + "\n\n" + kpi_md
        safe_write(OUTPUT_PATH, updated)
        print(f"[✓] Updated: {OUTPUT_PATH.name} ({weighted_oi/1e9:.2f}B)")
    except Exception as e:
        print(f"[✗] Write error: {e}")

if __name__ == "__main__":
    main()
