# gen_tracker_v2.py – DR-P2 Integration (Phase-Aware Tracker Sync)
# Usage: cd docs/autogen ; python gen_tracker_v2.py
import sqlite3
import pandas as pd
import json
import os
from datetime import datetime
from pathlib import Path
import matplotlib.pyplot as plt

CONTEXT_PATH = Path(r"E:\Trading\crypto-futures-dashboard\docs\project_context_v3.json")
OUTPUT_PATH = Path(r"E:\Trading\crypto-futures-dashboard\docs\quant_progress_tracker.md")
PLOT_PATH = Path(r"E:\Trading\crypto-futures-dashboard\docs\plots\z_trend.png")

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
    db_abs = Path(r"E:\Trading\crypto-futures-dashboard") / Path(db_path)

    phase = ctx.get("phase", "Unknown")
    uptime = ctx.get("uptime_pct", 0)
    files_changed = int(os.getenv("FILES_CHANGED", "0"))

    print(f"[i] DB: {db_abs} | OUT: {OUTPUT_PATH}")

    with sqlite3.connect(db_abs) as con:
        df = pd.read_sql("SELECT z_score, timestamp FROM metrics ORDER BY timestamp DESC LIMIT 100", con)

    if df.empty:
        print("[✗] No metrics data found.")
        return

    avg_z = df['z_score'].mean()
    total_rows = len(df)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    row = f"| {phase} | {uptime:.1f}% | AvgZ {avg_z:.2f} ({files_changed} files, {ts}) |\n"

    with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
        f.write(row)

    print(f"[✓] Tracker updated: {row.strip()}")

    # Plot rolling Z-trend
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    df['z_roll'] = df['z_score'].rolling(20).mean()

    PLOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(6, 3))
    plt.plot(df['z_roll'], label='Z-Roll Mean')
    plt.title('Z-Score Rolling Mean (Last 100)')
    plt.xlabel('Timestamp')
    plt.ylabel('Z-Score')
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=100)
    plt.close()

    print(f"[✓] Plot saved: {PLOT_PATH}")

if __name__ == "__main__":
    main()
