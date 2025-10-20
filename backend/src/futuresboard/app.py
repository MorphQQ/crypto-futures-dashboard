from __future__ import annotations

import json
import logging
import pathlib
import sqlite3
from logging.handlers import RotatingFileHandler

import argparse
import os
from datetime import datetime
from dotenv import load_dotenv  # .env load (API_KEY, AUTO_SCRAPE_INTERVAL)
import time  # For continuity sync loop sleep

# === Phase 3 unified environment setup ===
load_dotenv()
DB_PATH = os.getenv("DB_PATH", "backend/src/futuresboard/futures.db")
CONTINUITY_DOCS = os.getenv("CONTINUITY_DOCS", "docs/continuity_state.json")
PHASE = os.getenv("PHASE", "P3 - Weighted OI + Top L/S + Alerts")

# Sys.path hack for relative imports in script mode (Pylance/VSCode resolves – top before imports)
from sys import path
path.append(os.path.dirname(os.path.dirname(__file__)))  # Add src parent (backend)

from flask import Flask, redirect, request, render_template, current_app, jsonify  # Added jsonify
from flask_cors import CORS  # For frontend fetches
from flask_socketio import SocketIO  # WS for Phase 1 refreshes

# Relative imports (fix absolute fail; from .)
from . import blueprint
from . import db
from .config import Config
from .db import get_latest_metrics, get_metrics_by_symbol, Metric  # Relative: Metric for cols serialize
from .metrics import metrics_bp  # Relative

socketio = None  # Module-level export for scraper import (set in init_app)


def clear_trailing():
    rp = request.path 
    if rp != "/" and rp.endswith("/") and not rp.startswith('/socket.io'):
        return redirect(rp[:-1])


def init_app(config: Config | None = None):
    global socketio  # Set module-level
    if config is None:
        config = Config.from_config_dir(pathlib.Path.cwd())

    app = Flask(__name__)
    
    # Config load: Handle path str or Config object
    if isinstance(config, str):  # Raw path str
        with open(config, 'r') as f:
            config_data = json.load(f)
        app.config.from_mapping(**config_data)
    elif hasattr(config, 'path'):  # Config with path attr
        with open(config.path, 'r') as f:
            config_data = json.load(f)
        app.config.from_mapping(**config_data)
    else:  # Config object
        app.config.from_object(config)
    
    # Ensure defaults if missing (post-load)
    if 'symbols' not in app.config:
        app.config['symbols'] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    if 'API_BASE_URL' not in app.config:
        app.config['API_BASE_URL'] = 'https://fapi.binance.com'
    app.config['sandbox'] = app.config.get('TEST_MODE', False)  # Map: false=live, true=sandbox
    
    # Logging setup (post-app; propagate to child loggers scraper/metrics/db)
    log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')  # backend/logs
    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(os.path.join(log_dir, 'app.log'), maxBytes=10*1024*1024, backupCount=3)
    file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
    file_handler.setLevel(logging.INFO)
    logger = logging.getLogger(__name__)
    logger.addHandler(file_handler)
    logger.setLevel(logging.INFO)

    # Root propagate (capture child loggers)
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.INFO)
    root_logger.propagate = True
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = True

    print("Logging setup complete - check backend/logs/app.log")  # Test entry
    
    app.url_map.strict_slashes = False
    db.init_app(app)
    app.before_request(clear_trailing)
    app.register_blueprint(blueprint.app)
    
    # Add CORS early for all routes
    CORS(app, origins=['http://localhost:5173'])  # Frontend allowed

    # New: History routes (for charts/frontend) - early, no cycle
    @app.route('/api/metrics/history')
    def api_metrics_history():
        limit = request.args.get('limit', 50, type=int)
        data = get_latest_metrics(limit)
        # Clean JSON serialize (cols only; no '_sa_instance_state')
        serialized = []
        for row in data:
            row_dict = {col.name: getattr(row, col.name) for col in row.__table__.columns}
            row_dict['time'] = row.timestamp.timestamp() if row.timestamp else 0  # Unix s fallback
            serialized.append(row_dict)
        return jsonify(serialized)  # [ {'time': 1760764607.87, 'price': 69163.63, 'global_ls_5m': 1.82, ...} ]

    @app.route('/api/metrics/<symbol>/history')
    def api_symbol_history(symbol):
        tf = request.args.get('tf', '5m')  # Default '5m' string (no 0)
        limit = request.args.get('limit', 24, type=int)  # Hourly default
        data = get_metrics_by_symbol(symbol, limit)  # List[Metric]
        # Clean JSON serialize (cols only; no '_sa_instance_state')
        serialized = []
        for row in data:
            row_dict = {col.name: getattr(row, col.name) for col in row.__table__.columns}
            row_dict['time'] = row.timestamp.timestamp() if row.timestamp else 0  # Unix s fallback
            serialized.append(row_dict)
        return jsonify(serialized)  # [ {'time': 1760764607.87, 'price': 69163.63, 'global_ls_5m': 1.82, ...} ]

    @app.route("/health")
    def health():
        state_path = CONTINUITY_DOCS
        state_data = {}
        try:
            if os.path.exists(state_path):
                with open(state_path, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
        except Exception as e:
            state_data = {"error": f"Could not read state: {str(e)}"}

        response = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "version": "v0.3.3",
            "continuity": {
                "phase": state_data.get("phase", PHASE),
                "uptimePct": state_data.get("uptimePct", 0),
                "backend": state_data.get("backend", "unknown"),
                "last_sync": state_data.get("timestamp", "N/A"),
            },
        }
        return jsonify(response)
        
    # Lazy metrics import + route add (breaks cycle: post-app init)
    from .metrics import add_metrics_route, metrics_bp  # Relative: Import here + metrics_bp for register
    add_metrics_route(app)

    # Register metrics_bp with prefix (for /api/metrics, /api/health, /api/<symbol>/history)
    app.register_blueprint(metrics_bp, url_prefix='/api')  # Prefix /api (metrics_bp routes '/' → /api/metrics)

    # Init SocketIO (Phase 1 WS for metrics_update)
    socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)  # * for dev; restrict prod

    # Lazy scraper import + auto_scrape (breaks cycle: after app setup)
    if not config.DISABLE_AUTO_SCRAPE:
        from . import scraper  # Relative: Import here (post-app init)
        scraper.auto_scrape(app)

    #print(app.url_map)  # Debug: Show all routes (expect /health GET)
        import threading

    def continuity_sync_loop():
        """
        Continuity Sync Loop:
        Writes docs/continuity_state.json and docs/continuity_log.json every 5 minutes,
        tracking uptime percentage and detecting downtime gaps between runs.
        """
        phase = os.getenv("PHASE", "P3 - Weighted OI + Top L/S + Alerts")
        state_file = os.path.join(os.path.dirname(__file__), "../../../docs/continuity_state.json")
        log_file = os.path.join(os.path.dirname(__file__), "../../../docs/continuity_log.json")
        os.makedirs(os.path.dirname(state_file), exist_ok=True)

        start_time = time.time()
        total_runtime = 0.0
        downtime = 0.0
        last_sync_time = None

        # Load previous state (to maintain continuity between restarts)
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    prev_state = json.load(f)
                    total_runtime = prev_state.get("total_runtime", 0.0)
                    downtime = prev_state.get("downtime", 0.0)
                    last_sync_time = prev_state.get("timestamp")
            except Exception:
                pass

        # Check if downtime occurred since last run
        if last_sync_time:
            try:
                last_dt = datetime.fromisoformat(last_sync_time)
                gap_sec = (datetime.now() - last_dt).total_seconds()
                if gap_sec > 600:  # >10 minutes gap considered downtime
                    downtime += gap_sec
                    print(f"[Continuity] Downtime gap detected: {gap_sec:.0f}s added")
            except Exception:
                pass

        while True:
            try:
                now = datetime.now()
                now_str = now.isoformat(timespec="seconds")
                uptime_sec = time.time() - start_time
                total_runtime += uptime_sec

                # Calculate uptime percentage
                uptimePct = 0.0
                if total_runtime > 0:
                    uptimePct = max(0, min(100, ((total_runtime - downtime) / total_runtime) * 100))

                state = {
                    "timestamp": now_str,
                    "phase": phase,
                    "backend": "healthy",
                    "uptimePct": round(uptimePct, 2),
                    "total_runtime": round(total_runtime, 2),
                    "downtime": round(downtime, 2),
                    "status": "active"
                }

                # Write state file
                with open(state_file, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)

                # Load and append continuity log
                logs = []
                if os.path.exists(log_file):
                    try:
                        with open(log_file, "r", encoding="utf-8") as f:
                            logs = json.load(f)
                    except Exception:
                        logs = []

                # Check timestamp gap between last and current
                if logs:
                    try:
                        prev_ts = datetime.fromisoformat(logs[-1]["timestamp"])
                        gap = (now - prev_ts).total_seconds()
                        if gap > 600:  # >10 minutes since last log
                            downtime += gap
                            state["downtime"] = round(downtime, 2)
                            print(f"[Continuity] Gap {gap:.0f}s detected — added to downtime")
                    except Exception:
                        pass

                logs.append(state)
                logs = logs[-100:]  # Keep latest 100 entries
                with open(log_file, "w", encoding="utf-8") as f:
                    json.dump(logs, f, indent=2)

                app.logger.info(
                    f"[Continuity] Sync updated at {now_str} phase={phase} uptime={state['uptimePct']}% downtime={state['downtime']}"
                )

                start_time = time.time()

            except Exception as e:
                app.logger.warning(f"[Continuity] Sync loop error: {e}")

            time.sleep(300)  # Every 5 minutes

    # Start continuity background sync
    threading.Thread(target=continuity_sync_loop, daemon=True).start()
    app.logger.info("Continuity sync loop started")

    return app



    threading.Thread(target=continuity_sync_loop, daemon=True).start()
    app.logger.info("Continuity sync loop started")
    return app

def main():
    load_dotenv()  # Load .env early (keys for CCXT/metrics.py)
    
    parser = argparse.ArgumentParser(description='Crypto Futures Dashboard (Modified v0.3.3)')
    parser.add_argument('--port', type=int, default=5000, help='Port to run on (default: 5000)')
    args = parser.parse_args()
    
    # Sys.path hack for relative imports in script mode (Pylance/VSCode resolves)
    from sys import path
    path.append(os.path.dirname(os.path.dirname(__file__)))  # Add src parent (backend)
    
    # Init app (uses config.json + .env overrides)
    print("Initializing app...")  # Debug: Aligns "Importing..." from init_app
    app = init_app()  # Calls existing init_app (bp register, scraper auto)
    
    # Health/debug post-init (roadmap: /health route via metrics_bp)
    print(f"metrics_bp imported successfully (pairs: {app.config.get('symbols', [])})")
    print(f"Starting SocketIO on http://0.0.0.0:{args.port} (debug mode)...")
    
    # Run (SocketIO handles WS/HTTP; allow_unsafe_werkzeug for Windows/debug)
    socketio.run(app, host='0.0.0.0', port=args.port, debug=True, allow_unsafe_werkzeug=True)

if __name__ == '__main__':
    main()