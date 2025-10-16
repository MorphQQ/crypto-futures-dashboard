# backend/seed_metrics.py
import asyncio
import os
from src.futuresboard.metrics import get_all_metrics
from src.futuresboard.db import save_metrics, create_metrics_table, get_db, close_db, Base, engine
from flask import Flask
from src.futuresboard.config import Config
import pathlib
import json

async def main():
    # Temp app for context (loads config for DATABASE, inits g.db for raw query)
    cfg_path = pathlib.Path(os.path.dirname(os.path.dirname(__file__)))  # backend root
    cfg = Config.from_config_dir(cfg_path)
    temp_app = Flask(__name__)
    temp_app.config.from_object(cfg)  # Load Config to app.config (DATABASE etc.)
    temp_app.config['DATABASE'] = str(cfg.DATABASE)  # Str for get_db sqlite.connect
    with temp_app.app_context():  # Standalone context for raw DB funcs
        Base.metadata.create_all(engine)  # ORM tables (safe if exists)
        create_metrics_table()  # Raw CREATE/ALTER (uses g.db)
        metrics = await get_all_metrics('5m')  # Standalone fetches
        # Filter errors (e.g., expired ALPACAUSDT, BASUSDT ticker fail)
        valid_metrics = [m for m in metrics if 'error' not in m]
        saved = save_metrics(valid_metrics)  # ORM merge (pre-calc deltas)
        print(f"Seeded {len(valid_metrics)} valid pairs, saved {saved} to DB")
        # Sample print (last row for sanity)
        if valid_metrics:
            print("Sample last row:", valid_metrics[-1])  # e.g., {'symbol': 'ADA', 'oi_abs_usd': 14100000000, 'global_ls_5m': 2.41}
        # Quick count
        import sqlite3
        conn = sqlite3.connect(str(cfg.DATABASE))
        count = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        conn.close()
        print(f"DB count: {count}")

if __name__ == "__main__":
    asyncio.run(main())