# backend/seed_metrics.py
import asyncio
import os
import numpy as np
import random
from datetime import datetime, timezone
from src.futuresboard.db import save_metrics, create_metrics_table, Base, engine
from flask import Flask
from src.futuresboard.config import Config
import pathlib

# 2025 Proxies Scale (BTC $112k/$50B Changelly/CoinDCX $111.5k avg; ETH $4.2k/$20B $4.15k avg; SOL $185/$5B $184 avg; jitter ±10%)
mock_base = {
    'BTC': {'price': 112000, 'oi_abs_usd': 50e9, 'volume_24h': 100e9, 'global_ls': 2.39},
    'ETH': {'price': 4200, 'oi_abs_usd': 20e9, 'volume_24h': 50e9, 'global_ls': 1.89},
    'SOL': {'price': 185, 'oi_abs_usd': 5e9, 'volume_24h': 10e9, 'global_ls': 3.75},
    'BNB': {'price': 1200, 'oi_abs_usd': 3e9, 'volume_24h': 5e9, 'global_ls': 3.74},
    'XRP': {'price': 3.5, 'oi_abs_usd': 2e9, 'volume_24h': 8e9, 'global_ls': 3.34},
    'DOGE': {'price': 0.3, 'oi_abs_usd': 1.5e9, 'volume_24h': 15e9, 'global_ls': 1.93},
    'TRX': {'price': 0.6, 'oi_abs_usd': 1e9, 'volume_24h': 6e9, 'global_ls': 3.82},
    'AVAX': {'price': 60, 'oi_abs_usd': 2.5e9, 'volume_24h': 4e9, 'global_ls': 1.91},
    'LINK': {'price': 30, 'oi_abs_usd': 800e6, 'volume_24h': 3e9, 'global_ls': 1.15},
    'MATIC': {'price': 1.2, 'oi_abs_usd': 700e6, 'volume_24h': 2.5e9, 'global_ls': 2.71},
    'DOT': {'price': 12, 'oi_abs_usd': 600e6, 'volume_24h': 2e9, 'global_ls': 4.11},
    'LTC': {'price': 120, 'oi_abs_usd': 1.2e9, 'volume_24h': 3e9, 'global_ls': 3.25},
    'BCH': {'price': 600, 'oi_abs_usd': 900e6, 'volume_24h': 1.5e9, 'global_ls': 3.66},
    'XLM': {'price': 0.6, 'oi_abs_usd': 500e6, 'volume_24h': 1e9, 'global_ls': 2.88},
    'UNI': {'price': 12, 'oi_abs_usd': 400e6, 'volume_24h': 2e9, 'global_ls': 2.49},
    'ATOM': {'price': 20, 'oi_abs_usd': 300e6, 'volume_24h': 1.5e9, 'global_ls': 3.06},
    'XMR': {'price': 250, 'oi_abs_usd': 500e6, 'volume_24h': 1e9, 'global_ls': 1.60},
    'VET': {'price': 0.06, 'oi_abs_usd': 200e6, 'volume_24h': 800e6, 'global_ls': 2.40},
    'FIL': {'price': 6, 'oi_abs_usd': 400e6, 'volume_24h': 1.2e9, 'global_ls': 2.10},
    'PEPE': {'price': 0.000012, 'oi_abs_usd': 100e6, 'volume_24h': 5e9, 'global_ls': 2.95}
}
symbols = list(mock_base.keys())

def generate_mock(tf='5m'):
    metrics = []
    for sym in symbols:
        base = mock_base[sym]
        jitter = 1 + np.random.uniform(-0.1, 0.1)  # ±10%
        global_ls = base['global_ls'] + np.random.uniform(-0.5, 0.5)  # LS jitter
        m = {
            'symbol': sym,
            'Price': f"${base['price'] * jitter:.2f}",  # $112k jitter
            'oi_abs_usd': base['oi_abs_usd'] * jitter,  # Raw float for calc
            'OI_USD': f"${base['oi_abs_usd'] * jitter:.0f}",
            'Volume_24h': f"${base['volume_24h'] * jitter:.0f}",
            'Market_Cap': f"${base['volume_24h'] * 10 * jitter:.0f}",  # Proxy cap
            'Top_LS': 1.99 + np.random.uniform(-0.2, 0.2),  # 1.8-2.2
            'Top_LS_Positions': np.random.uniform(0.8, 1.2),
            'top_ls_accounts': np.random.uniform(1.5, 2.5),
            'Long_Account_Pct': 55 + np.random.uniform(-5, 5),
            'Short_Account_Pct': 45 + np.random.uniform(-5, 5),
            'OI_Change_24h_Pct': f"{np.random.uniform(-1, 1):.2f}%",
            'Price_Change_24h_Pct': f"{np.random.uniform(-1, 1):.2f}%",
            'Volume_Change_24h_Pct': f"{np.random.uniform(-5, 5):.2f}%",
            'cvd': np.random.uniform(-1e9, 1e9),  # $B range
            'imbalance': np.random.uniform(-5, 5),
            'funding': np.random.uniform(-0.01, 0.01),
            'rsi': np.random.uniform(30, 70),  # Stub
            'timestamp': datetime.now(timezone.utc).timestamp() + random.randint(0, 60)  # Jitter s
        }
        # Tf variants (base global_ls for all tfs in JSON; no pre-access)
        for t in ['5m', '15m', '30m', '1h']:
            m[f'Global_LS_{t}'] = global_ls + np.random.uniform(-0.2, 0.2)
            m[f'OI_Change_{t}_Pct'] = f"{np.random.uniform(-0.5, 0.5):.2f}%"
            m[f'Price_Change_{t}_Pct'] = f"{np.random.uniform(-0.5, 0.5):.2f}%"
        metrics.append(m)
    return metrics

async def main():
    cfg_path = pathlib.Path(os.path.dirname(os.path.dirname(__file__)))  # backend root
    cfg = Config.from_config_dir(cfg_path)
    temp_app = Flask(__name__)
    temp_app.config.from_object(cfg)
    temp_app.config['DATABASE'] = str(cfg.DATABASE)
    with temp_app.app_context():
        Base.metadata.create_all(engine)
        create_metrics_table()
        metrics = generate_mock('5m')  # Scaled mocks
        saved = save_metrics(metrics, timeframe='5m')  # Save w/ calc
        print(f"Seeded {len(metrics)} valid pairs, saved {saved} to DB")
        if metrics:
            print("Sample last row:", metrics[-1])  # e.g., {'symbol': 'PEPE', 'Price': '$0.00', 'oi_abs_usd': 110000000, ... 'rsi': 55}
        import sqlite3
        conn = sqlite3.connect(str(cfg.DATABASE))
        count = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        conn.close()
        print(f"DB count: {count}")

if __name__ == "__main__":
    asyncio.run(main())