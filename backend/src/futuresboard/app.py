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

# ----------------------------------------------------------------------
# Load env early
# ----------------------------------------------------------------------
load_dotenv()

# ----------------------------------------------------------------------
# Force UTF-8 for console + file logging (Windows fix)
# ----------------------------------------------------------------------
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ----------------------------------------------------------------------
# Paths & constants
# ----------------------------------------------------------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LOG_DIR = (REPO_ROOT / "logs").resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = os.getenv("DB_PATH", str(REPO_ROOT / "backend" / "src" / "futuresboard" / "futures.db"))
CONTINUITY_DOCS = os.getenv("CONTINUITY_DOCS", str(REPO_ROOT / "docs" / "continuity_state.json"))
PHASE = os.getenv("PHASE", "P3 - Weighted OI + Top L/S + Alerts")
AUTO_SCRAPE_INTERVAL = int(os.getenv("INTERVAL", os.getenv("AUTO_SCRAPE_INTERVAL", "30")))

# Ensure backend/src is importable
from sys import path as sys_path
sys_path.append(str(pathlib.Path(__file__).parent.parent))

from flask import Flask, redirect, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

# ----------------------------------------------------------------------
# Logging setup
# ----------------------------------------------------------------------
logger = logging.getLogger("futuresboard")
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler(
    str(LOG_DIR / "app.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",  # ensure UTF-8
)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]"))
logger.addHandler(file_handler)

console = logging.StreamHandler(sys.stdout)
console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
console.setLevel(logging.INFO)
logger.addHandler(console)

socketio: SocketIO | None = None  # exported below if started

# ----------------------------------------------------------------------
# Local imports
# ----------------------------------------------------------------------
from . import db as db_mod
from .config import Config


def serialize_metric_row(m):
    """Converts ORM Metric object or sqlite3.Row to plain dict for JSON output."""
    try:
        if hasattr(m, "__dict__"):
            data = {k: v for k, v in m.__dict__.items() if not k.startswith("_")}
        elif isinstance(m, dict):
            data = m
        elif hasattr(m, "keys"):
            data = {k: m[k] for k in m.keys()}
        else:
            return {}
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


# ----------------------------------------------------------------------
# Init Flask app
# ----------------------------------------------------------------------
def init_app(config: Config | str | None = None):
    print("[DEBUG] init_app() start")
    global socketio
    app = Flask(__name__, static_folder=None)
    print("[DEBUG] Flask app created")

    # Config loading
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
        app.config["symbols"] = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")
        app.config["API_BASE_URL"] = os.getenv("API_BASE_URL", "https://fapi.binance.com")
        app.config["TEST_MODE"] = os.getenv("TEST_MODE", "false").lower() == "true"

    # Logging integration
    app.logger.handlers.clear()
    app.logger.propagate = True
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console)

    # CORS (local only)
    CORS(app, origins=["http://localhost:5173"])

    # DB init
    db_mod.init_app(app)
    print("[DEBUG] DB initialized")

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
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

    # Import metrics blueprint lazily
    try:
        print("[DEBUG] Importing metrics blueprint...")
        from .metrics import metrics_bp, add_metrics_route  # type: ignore
        add_metrics_route(app)
        app.register_blueprint(metrics_bp, url_prefix="/api")
        print("[DEBUG] Metrics blueprint registered successfully")
    except Exception as e:
        print("[ERROR] Failed to register metrics blueprint:", e)
        import traceback; traceback.print_exc()
        return None

    # SocketIO setup
    async_mode = None
    try:
        import eventlet
        async_mode = "eventlet"
    except Exception:
        try:
            import gevent
            async_mode = "gevent"
        except Exception:
            async_mode = "threading"

    socketio = SocketIO(app, cors_allowed_origins="http://localhost:5173", async_mode=async_mode, logger=False)

    # ------------------------------------------------------------------
    # Background Tasks
    # ------------------------------------------------------------------
    disable_auto = os.getenv("DISABLE_AUTO_SCRAPE", "false").lower() == "true"
    if not disable_auto:
        from . import scraper
        try:
            if socketio and hasattr(socketio, "start_background_task"):
                socketio.start_background_task(scraper.auto_scrape, app)
            else:
                import threading
                threading.Thread(target=scraper.auto_scrape, args=(app,), daemon=True).start()
            app.logger.info("Started scraper background task")
        except Exception as e:
            app.logger.warning(f"Failed to start scraper background task: {e}")

    # Continuity sync loop
    def continuity_sync_loop():
        from .db import get_latest_metrics
        state_file = pathlib.Path(CONTINUITY_DOCS)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        log_file = state_file.parent / "continuity_log.json"
        start_time = time.time()
        total_runtime = downtime = 0.0
        if state_file.exists():
            try:
                p = json.loads(state_file.read_text(encoding="utf-8"))
                total_runtime = p.get("total_runtime", 0.0)
                downtime = p.get("downtime", 0.0)
            except Exception:
                pass

        while True:
            try:
                now = datetime.utcnow()
                now_str = now.isoformat(timespec="seconds")
                elapsed = time.time() - start_time
                total_runtime += elapsed
                db_ok = False
                try:
                    recent = get_latest_metrics(limit=1)
                    db_ok = len(recent) > 0
                except Exception:
                    pass
                uptime_pct = max(0.0, min(100.0, ((total_runtime - downtime) / total_runtime) * 100.0)) if total_runtime else 0
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
                app.logger.info(f"[Continuity] Sync updated at {now_str} phase={PHASE} uptime={state['uptimePct']}% backend={state['backend']}")
                start_time = time.time()
            except Exception as e:
                app.logger.warning(f"[Continuity] loop error: {e}")
            time.sleep(300)

    # Quant summary emitter
    def emit_quant_summary_loop():
        import sqlite3, time
        while True:
            try:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("""
                    SELECT symbol, timeframe, oi_z, ls_delta_pct, imbalance,
                           funding, confluence_score, bias, updated_at
                    FROM quant_summary
                    ORDER BY updated_at DESC
                    LIMIT 50
                """)
                rows = cur.fetchall()
                conn.close()
                if not rows:
                    time.sleep(30)
                    continue
                keys = ["symbol", "timeframe", "oi_z", "ls_delta_pct", "imbalance",
                        "funding", "confluence_score", "bias", "updated_at"]
                data = [dict(zip(keys, r)) for r in rows]
                if socketio:
                    socketio.emit("quant_update", {"data": data, "ts": datetime.utcnow().isoformat()})
                    app.logger.info(f"[QuantWS] Emitted quant_update ({len(data)} rows)")
            except Exception as e:
                app.logger.warning(f"[QuantWS] Emit loop error: {e}")
            time.sleep(30)

    # Start background loops
    try:
        if socketio and hasattr(socketio, "start_background_task"):
            socketio.start_background_task(continuity_sync_loop)
            socketio.start_background_task(emit_quant_summary_loop)
        else:
            import threading
            threading.Thread(target=continuity_sync_loop, daemon=True).start()
        app.logger.info("Continuity + QuantWS loops started")
    except Exception as e:
        app.logger.warning(f"Continuity sync failed to start: {e}")

    # Metrics API
    from .db import get_latest_metrics, get_metrics_by_symbol

    @app.route('/api/metrics/history')
    def api_metrics_history():
        limit = request.args.get('limit', 100, type=int)
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
        try:
            rows = get_metrics_by_symbol(symbol, limit=limit)
            serialized = [serialize_metric_row(m) for m in rows if m]
            return jsonify(serialized)
        except Exception as e:
            app.logger.warning(f"[API] /metrics/{symbol}/history failed: {e}")
            import traceback; traceback.print_exc()
            return jsonify([]), 500

    # Quant API
    from .quant_engine import compute_quant_metrics
    import sqlite3

    @app.route("/api/quant/summary")
    def api_quant_summary():
        limit = request.args.get("limit", 100, type=int)
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, timeframe, oi_z, ls_delta_pct, imbalance,
                   funding, confluence_score, bias, updated_at
            FROM quant_summary
            ORDER BY updated_at DESC
            LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        keys = ["symbol", "timeframe", "oi_z", "ls_delta_pct", "imbalance",
                "funding", "confluence_score", "bias", "updated_at"]
        if not rows:
            data = compute_quant_metrics(limit=limit)
            return jsonify({"status": "computed", "data": data})
        data = [dict(zip(keys, r)) for r in rows]
        return jsonify({"status": "ok", "data": data})

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
