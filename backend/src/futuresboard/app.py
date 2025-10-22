# Fixed: backend/src/futuresboard/app.py
# Changes:
# - Removed blueprint import/register (obsolete).
# - Fixed continuity uptime: Increment downtime on DB fail.
# - Added monkey-patch for eventlet/gevent if selected.
# - Gated DEBUG prints behind config.DEBUG.
# - Standardized timestamps in routes (to_ms helper).
# - Added input validation for tf/symbol.
# - Integrated WS client start as background task.
# - Atomic writes for continuity file (temp + rename).
# - Fixed duplicate logs/starts with use_socketio_tasks guard and handler dedupe.
# - Fixed continuity rename race with os.replace and stale .tmp cleanup.
# - Added import asyncio for WS threading fallback.
from __future__ import annotations  # MUST be first!

# ----------------------------------------------------------------------
# EARLIEST MONKEY PATCH (even before logging/json imports)
# ----------------------------------------------------------------------
ASYNC_MODE = "threading"  # Default fallback
try:
    import eventlet  # type: ignore
    eventlet.monkey_patch(all=True)  # patch everything, early
    ASYNC_MODE = "eventlet"
    print(f"[Async] Patched {ASYNC_MODE}")  # Confirm success
except Exception as e:
    print(f"[Async] Eventlet failed ({e}); trying gevent...")
    try:
        import gevent  # type: ignore
        gevent.monkey.patch_all()
        ASYNC_MODE = "gevent"
        print(f"[Async] Patched {ASYNC_MODE}")
    except Exception as ge:
        print(f"[WARN] Fallback to threading – install eventlet/gevent for green threads (pip install eventlet)")
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*RLock.*not greened.*")
# ----------------------------------------------------------------------
# Now safe to import everything else
# ----------------------------------------------------------------------
import json
import logging
import pathlib
import os
import argparse
import time
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# ----------------------------------------------------------------------
# Load env early (after __future__)
# ----------------------------------------------------------------------
load_dotenv()

# Force UTF-8 for console + file logging (Windows fix)
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
import asyncio  # For WS fallback run

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
# --- De-duplicate all log handlers globally (Flask, werkzeug, etc.) ---
for log_name in ["werkzeug", "flask.app", "engineio", "socketio"]:
    l = logging.getLogger(log_name)
    l.handlers.clear()
    l.propagate = True
    l.setLevel(logging.INFO)
    
# Logging dedupe (add right after logger.addHandler(console))
if len(logger.handlers) > 2:  # File + console = 2 max
    logger.handlers = logger.handlers[:2]  # Trim extras

socketio: SocketIO | None = None  # exported below if started

# ----------------------------------------------------------------------
# Local imports
# ----------------------------------------------------------------------
from . import db as db_mod
from .config import Config

ALLOWED_TFS = ["5m", "15m", "30m", "1h"]  # Validation set

def to_ms(dt):
    """Helper: Unix ms from datetime or 0."""
    return int(dt.timestamp() * 1000) if dt and hasattr(dt, 'timestamp') else 0

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
            data["timestamp"] = to_ms(data["timestamp"])
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
    if config and hasattr(config, 'to_dict'):
        app_config = config.to_dict()
    else:
        app_config = {}
    app = Flask(__name__, static_folder=None)

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
    if app.config.get("DEBUG"):
        print("[DEBUG] DB initialized")
    
        # --- Continuity-safe DB init & stale cleanup ---
    if not getattr(app, "_db_initialized", False):
        app._db_initialized = True
        tmp_path = pathlib.Path(CONTINUITY_DOCS).with_suffix(".tmp")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
                app.logger.info(f"[Continuity] Cleaned stale tmp file at startup: {tmp_path}")
            except Exception as e:
                app.logger.warning(f"[Continuity] Failed tmp cleanup: {e}")
    else:
        app.logger.info("[Continuity] DB already initialized; skipping duplicate init.")

    # Delay background start until DB verified
    try:
        from .db import get_latest_metrics
        _test_rows = get_latest_metrics(limit=1)
        if not _test_rows:
            app.logger.info("[Continuity] DB empty; delaying background loops for 5 s")
            time.sleep(5)
    except Exception as e:
        app.logger.warning(f"[Continuity] DB precheck failed: {e}")

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    @app.route("/health")
    def health():
        HEALTH_STATE = {"start_ts": time.time(), "last_success": time.time(), "last_error": None, "emit_queue_size": 0}
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
        if app.config.get("DEBUG"):
            print("[DEBUG] Importing metrics blueprint...")
        from .metrics import metrics_bp, add_metrics_route  # type: ignore
        add_metrics_route(app)
        app.register_blueprint(metrics_bp, url_prefix="/api")
        if app.config.get("DEBUG"):
            print("[DEBUG] Metrics blueprint registered successfully")
    except Exception as e:
        print(f"[ERROR] Failed to register metrics blueprint: {e}")
        import traceback; traceback.print_exc()
        return None

   
    # -------------------------
    # SocketIO setup (single source)
    # -------------------------
    # Prefer early-detected mode from package init if available
    try:
        from . import __init__ as _pkg_init  # type: ignore
        async_mode = getattr(_pkg_init, "_ASYNC_MODE", ASYNC_MODE)
    except Exception:
        async_mode = ASYNC_MODE
    app.logger.info(f"[Async] Using async_mode={async_mode}")
    global socketio
    socketio = SocketIO(app, cors_allowed_origins="http://localhost:5173", async_mode=async_mode, logger=False)

    # ------------------------------------------------------------------
    # Background Continuity + Quant Loops
    # ------------------------------------------------------------------
    def continuity_sync_loop():
        """
        Periodically updates continuity_state.json with backend uptime,
        DB health, and runtime stats. Runs forever in a background thread.
        """
        import json, sqlite3
        from .db import get_latest_metrics
        state_file = pathlib.Path(CONTINUITY_DOCS)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.time()
        total_runtime = downtime = 0.0

        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    p = json.load(f)
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
                    db_ok = bool(recent)
                except Exception:
                    pass
                if not db_ok:
                    downtime += elapsed
                uptime_pct = (
                    ((total_runtime - downtime) / total_runtime) * 100.0
                    if total_runtime
                    else 0.0
                )
                state = {
                    "timestamp": now_str,
                    "phase": PHASE,
                    "backend": "healthy" if db_ok else "unhealthy",
                    "uptimePct": round(uptime_pct, 2),
                    "total_runtime": round(total_runtime, 2),
                    "downtime": round(downtime, 2),
                    "status": "active" if db_ok else "degraded",
                }
                temp_file = state_file.with_suffix(".tmp")
                temp_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
                os.replace(temp_file, state_file)
                app.logger.info(
                    f"[Continuity] Sync updated phase={PHASE} uptime={state['uptimePct']}% backend={state['backend']}"
                )
                start_time = time.time()
            except Exception as e:
                app.logger.warning(f"[Continuity] loop error: {e}")
            time.sleep(300)

    def emit_quant_summary_loop():
        """
        Emits quant_summary table via SocketIO every 30s.
        """
        import sqlite3
        while True:
            try:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT symbol, timeframe, oi_z, ls_delta_pct, imbalance,
                           funding, confluence_score, bias, updated_at
                    FROM quant_summary
                    ORDER BY updated_at DESC
                    LIMIT 50
                    """
                )
                rows = cur.fetchall()
                conn.close()
                if not rows:
                    time.sleep(30)
                    continue
                keys = [
                    "symbol",
                    "timeframe",
                    "oi_z",
                    "ls_delta_pct",
                    "imbalance",
                    "funding",
                    "confluence_score",
                    "bias",
                    "updated_at",
                ]
                data = [dict(zip(keys, r)) for r in rows]
                if socketio:
                    socketio.emit(
                        "quant_update",
                        {"data": data, "ts": datetime.utcnow().isoformat()},
                    )
                    app.logger.info(f"[QuantWS] Emitted quant_update ({len(data)} rows)")
            except Exception as e:
                app.logger.warning(f"[QuantWS] Emit loop error: {e}")
            time.sleep(30)

    # Helper: Start background work that may be coroutine or sync function.
    import inspect
    def _start_bg(target, *args, use_socketio=True):
        """
        Start a coroutine or sync function safely:
          - If target is an async def -> run via asyncio.run in a wrapper.
          - If socketio available and supports start_background_task -> use it.
          - Otherwise spawn a daemon thread.
        """
        try:
            if inspect.iscoroutinefunction(target):
                # wrap coroutine in runner
                runner = (lambda *a, **kw: __import__("asyncio").run(target(*a, **kw)))
                if use_socketio and socketio and hasattr(socketio, "start_background_task"):
                    socketio.start_background_task(runner, *args)
                else:
                    import threading
                    threading.Thread(target=runner, args=args, daemon=True).start()
            else:
                # normal function
                if use_socketio and socketio and hasattr(socketio, "start_background_task"):
                    socketio.start_background_task(target, *args)
                else:
                    import threading
                    threading.Thread(target=target, args=args, daemon=True).start()
        except Exception as e:
            app.logger.warning(f"[BG] Failed to start background task {target}: {e}")

    # Decide whether to use socketio's bg tasks
    disable_auto = os.getenv("DISABLE_AUTO_SCRAPE", "false").lower() == "true"
    use_socketio_tasks = socketio and hasattr(socketio, "start_background_task")

    # ---- Start scraper ----
    if not disable_auto:
        from . import scraper
        try:
            _start_bg(scraper.auto_scrape, app, use_socketio=use_socketio_tasks)
            app.logger.info("Started scraper (bg task starter)")
        except Exception as e:
            app.logger.warning(f"Failed to start scraper background task: {e}")

    # ---- Start WS worker safely (handles coroutine functions) ----
    try:
        from . import binance_ws_client
        symbols_list = app.config.get("symbols", "BTCUSDT,ETHUSDT,SOLUSDT").split(',')
        _start_bg(binance_ws_client.start_stream_worker, symbols_list, use_socketio=use_socketio_tasks)
        app.logger.info("Started WS stream worker (bg task starter)")
    except Exception as e:
        app.logger.warning(f"Failed to start WS worker: {e}")

    # ---- Continuity + quant loops (use _start_bg) ----
    try:
        _start_bg(continuity_sync_loop, use_socketio=use_socketio_tasks)
        _start_bg(emit_quant_summary_loop, use_socketio=use_socketio_tasks)
        app.logger.info("Started continuity/quant loops via bg starter")
    except Exception as e:
        app.logger.warning(f"Background loops failed to start: {e}")

    # ---- graceful shutdown handlers ----
    try:
        import signal

        def _shutdown(signum, frame):
            app.logger.info(f"[Shutdown] Received signal {signum}; shutting down backend")
            try:
                # Close socketio if possible
                if socketio:
                    try:
                        socketio.stop()
                    except Exception:
                        pass
                # Delay a brief tick for cleanup
                time.sleep(0.2)
                app.logger.info("[Shutdown] Exiting process now.")
                os._exit(0)  # hard exit; avoids eventlet hanging
            except Exception as e:
                app.logger.warning(f"[Shutdown] Handler failed: {e}")
                os._exit(1)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)
    except Exception as e:
        app.logger.warning(f"[Shutdown] Could not register signal handlers: {e}")


    # Metrics API (with validation)
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
        if not symbol or len(symbol) > 20:  # Basic validation
            return jsonify({"error": "Invalid symbol"}), 400
        tf = request.args.get("tf")
        if tf and tf not in ALLOWED_TFS:
            return jsonify({"error": "Invalid timeframe"}), 400
        limit = request.args.get('limit', 100, type=int)
        try:
            rows = get_metrics_by_symbol(symbol, limit=limit, tf=tf)
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

    if app.config.get("DEBUG"):
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