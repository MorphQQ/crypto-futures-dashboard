# backend/src/futuresboard/app.py
from __future__ import annotations

import json
import logging
import pathlib
import os
import argparse
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import sys

# Load env early
load_dotenv()

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root
LOG_DIR = (REPO_ROOT / "logs").resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = os.getenv("DB_PATH", str(REPO_ROOT / "backend" / "src" / "futuresboard" / "futures.db"))
CONTINUITY_DOCS = os.getenv("CONTINUITY_DOCS", str(REPO_ROOT / "docs" / "continuity_state.json"))
PHASE = os.getenv("PHASE", "P3 - Weighted OI + Top L/S + Alerts")
AUTO_SCRAPE_INTERVAL = int(os.getenv("INTERVAL", os.getenv("AUTO_SCRAPE_INTERVAL", "30")))

from sys import path as sys_path
sys_path.append(str(pathlib.Path(__file__).parent.parent))  # add backend/src

from flask import Flask, redirect, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

# local imports
from . import db as db_mod
from .config import Config

# logging
logger = logging.getLogger("futuresboard")
logger.setLevel(logging.INFO)
file_handler = RotatingFileHandler(str(LOG_DIR / "app.log"), maxBytes=10 * 1024 * 1024, backupCount=3)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]"))
logger.addHandler(file_handler)

socketio: SocketIO | None = None  # exported below if started

def serialize_metric_row(m):
    """
    Converts ORM Metric object or sqlite3.Row to plain dict for JSON output.
    """
    try:
        # ORM object
        if hasattr(m, "__dict__"):
            data = {k: v for k, v in m.__dict__.items() if not k.startswith("_")}
        # sqlite3.Row
        elif isinstance(m, dict):
            data = m
        elif hasattr(m, "keys"):  # sqlite3.Row-like
            data = {k: m[k] for k in m.keys()}
        else:
            return {}

        # Convert timestamp to ISO string if needed
        if "timestamp" in data and data["timestamp"] is not None:
            if hasattr(data["timestamp"], "isoformat"):
                data["timestamp"] = data["timestamp"].isoformat()
        return data
    except Exception as e:
        print(f"[WARN] serialize_metric_row failed: {e}")
        return {}

def clear_trailing():
    # remove trailing slash except for socket.io paths
    rp = request.path
    if rp != "/" and rp.endswith("/") and not rp.startswith("/socket.io"):
        return redirect(rp[:-1])

def init_app(config: Config | str | None = None):
    print("[DEBUG] init_app() start")
    global socketio
    app = Flask(__name__, static_folder=None)
    print("[DEBUG] Flask app created")
    # config handling: string path, Config instance, or default object
    if config is None:
        try:
            config = Config.from_config_dir(pathlib.Path.cwd() / "config")
        except Exception:
            config = None

    if isinstance(config, str):
        with open(config, "r", encoding="utf-8") as f:
            app.config.from_mapping(**json.load(f))
    elif hasattr(config, "to_dict"):
        app.config.from_mapping(**config.to_dict())
    elif config is not None:
        app.config.from_object(config)
    else:
        # minimal defaults
        app.config["symbols"] = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")
        app.config["API_BASE_URL"] = os.getenv("API_BASE_URL", "https://fapi.binance.com")
        app.config["TEST_MODE"] = os.getenv("TEST_MODE", "false").lower() == "true"

    # ensure a few keys
    app.config.setdefault("symbols", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    app.config.setdefault("API_BASE_URL", "https://fapi.binance.com")
    app.config.setdefault("TEST_MODE", False)

    # logging integration
    app.logger.handlers.clear()
    app.logger.propagate = True
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)

    # CORS (local only)
    CORS(app, origins=["http://localhost:5173"])

    # DB init
    db_mod.init_app(app)
    print("[DEBUG] DB initialized")

    # small helper: health route
    @app.route("/health")
    def health():
        state = {}
        try:
            if os.path.exists(CONTINUITY_DOCS):
                with open(CONTINUITY_DOCS, "r", encoding="utf-8") as f:
                    state = json.load(f)
        except Exception as e:
            app.logger.warning(f"Failed reading continuity state: {e}")
        resp = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
            "version": "v0.3.3",
            "continuity": {
                "phase": state.get("phase", PHASE),
                "uptimePct": state.get("uptimePct", 0),
                "backend": state.get("backend", "unknown"),
                "last_sync": state.get("timestamp", "N/A"),
            },
        }
        return jsonify(resp)

    # import metrics blueprint lazily (avoids import cycles)
    try:
        print("[DEBUG] Importing metrics blueprint...")
        from .metrics import metrics_bp, add_metrics_route  # type: ignore
        print("[DEBUG] Metrics module imported")
        add_metrics_route(app)
        print("[DEBUG] add_metrics_route() executed")
        app.register_blueprint(metrics_bp, url_prefix="/api")
        print("[DEBUG] Blueprint registered successfully")
    except Exception as e:
        print("[ERROR] Failed to register metrics blueprint:", e)
        import traceback; traceback.print_exc()
        return None

    # socket io initialization:
    # prefer eventlet/gevent if installed, otherwise fallback to threading mode
    async_mode = None
    try:
        import eventlet  # type: ignore
        async_mode = "eventlet"
    except Exception:
        try:
            import gevent  # type: ignore
            async_mode = "gevent"
        except Exception:
            async_mode = "threading"

    socketio = SocketIO(app, cors_allowed_origins="http://localhost:5173", async_mode=async_mode, logger=False, engineio_logger=False)

    # scraper import + auto start (if enabled)
    disable_auto = os.getenv("DISABLE_AUTO_SCRAPE", "false").lower() == "true"
    if not disable_auto:
        from . import scraper  # type: ignore
        # Prefer socketio.start_background_task for engines that support it (integrates with event loop)
        try:
            if socketio and hasattr(socketio, "start_background_task"):
                socketio.start_background_task(scraper.auto_scrape, app)
            else:
                import threading
                threading.Thread(target=scraper.auto_scrape, args=(app,), daemon=True).start()
            app.logger.info("Started scraper background task")
        except Exception as e:
            app.logger.warning(f"Failed to start scraper background task: {e}")

    # continuity sync loop: write small machine-readable state every 5 minutes
    def continuity_sync_loop():
        from .db import get_latest_metrics
        state_file = pathlib.Path(CONTINUITY_DOCS)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        log_file = state_file.parent / "continuity_log.json"
        start_time = time.time()
        total_runtime = 0.0
        downtime = 0.0
        last_ts = None

        # load previous
        if state_file.exists():
            try:
                p = json.loads(state_file.read_text(encoding="utf-8"))
                total_runtime = p.get("total_runtime", 0.0)
                downtime = p.get("downtime", 0.0)
                last_ts = p.get("timestamp")
            except Exception:
                pass

        while True:
            try:
                now = datetime.utcnow()
                now_str = now.isoformat(timespec="seconds")
                elapsed = time.time() - start_time
                total_runtime += elapsed
                # simple health: DB has at least one metric recently
                db_ok = False
                try:
                    recent = get_latest_metrics(limit=1)
                    db_ok = len(recent) > 0
                except Exception:
                    db_ok = False

                uptime_pct = 0.0
                if total_runtime > 0:
                    uptime_pct = max(0.0, min(100.0, ((total_runtime - downtime) / total_runtime) * 100.0))

                state = {
                    "timestamp": now_str,
                    "phase": PHASE,
                    "backend": "healthy" if db_ok else "unhealthy",
                    "uptimePct": round(uptime_pct, 2),
                    "total_runtime": round(total_runtime, 2),
                    "downtime": round(downtime, 2),
                    "status": "active" if db_ok else "degraded",
                }

                state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

                # append log (keeps last 100)
                logs = []
                if log_file.exists():
                    try:
                        logs = json.loads(log_file.read_text(encoding="utf-8"))
                    except Exception:
                        logs = []
                logs.append(state)
                logs = logs[-100:]
                log_file.write_text(json.dumps(logs, indent=2), encoding="utf-8")

                app.logger.info(f"[Continuity] Sync updated at {now_str} phase={PHASE} uptime={state['uptimePct']}% backend={state['backend']}")
                start_time = time.time()
            except Exception as e:
                app.logger.warning(f"[Continuity] loop error: {e}")
            time.sleep(300)

    # start continuity sync using socketio bg task where available
    try:
        if socketio and hasattr(socketio, "start_background_task"):
            socketio.start_background_task(continuity_sync_loop)
        else:
            import threading
            threading.Thread(target=continuity_sync_loop, daemon=True).start()
        app.logger.info("Continuity sync loop started")
    except Exception as e:
        app.logger.warning(f"Continuity sync failed to start: {e}")

    # remove trailing slash behavior
    app.before_request(clear_trailing)

     # ================================
    # FIXED: Metrics history endpoints
    # ================================

    from .db import get_latest_metrics, get_metrics_by_symbol

    @app.route('/api/metrics/history')
    def api_metrics_history():
        limit = request.args.get('limit', 100, type=int)
        tf = request.args.get('tf', None, type=str)

        try:
            rows = get_latest_metrics(limit=limit)
            serialized = [serialize_metric_row(m) for m in rows if m]
            return jsonify(serialized)
        except Exception as e:
            app.logger.warning(f"[API] /metrics/history failed: {e}")
            import traceback; traceback.print_exc()
            return jsonify([]), 500


    @app.route('/api/metrics/<symbol>/history')
    def api_symbol_history(symbol):
        limit = request.args.get('limit', 100, type=int)
        tf = request.args.get('tf', None, type=str)

        try:
            rows = get_metrics_by_symbol(symbol, limit=limit)
            serialized = [serialize_metric_row(m) for m in rows if m]
            return jsonify(serialized)
        except Exception as e:
            app.logger.warning(f"[API] /metrics/{symbol}/history failed: {e}")
            import traceback; traceback.print_exc()
            return jsonify([]), 500
    print("[DEBUG] init_app() returning app")
    return app


def main():
    parser = argparse.ArgumentParser(description="Crypto Futures Dashboard backend")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    app = init_app()
    if app is None:
        print("[FATAL] init_app() returned None – check imports or blueprint registration.")
        sys.exit(1)

    global socketio
    if socketio is None:
        raise RuntimeError("SocketIO not initialized (check for import failure).")

    print(f"Starting server on 0.0.0.0:{args.port} (async_mode={socketio.async_mode})")

    socketio.run(
        app,
        host="0.0.0.0",
        port=args.port,
        debug=False,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()
