# backend/src/futuresboard/app.py
from __future__ import annotations
import asyncio
import json
import os
import pathlib
import platform
import sys
import time
import logging
import argparse
import signal
import contextlib
from datetime import datetime
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from . import db

from quart import Quart, jsonify, request, redirect
from quart_cors import cors
import socketio
from .quant_engine import run_quant_loop, diagnostics_loop, signals_loop, confluence_loop

# Patch Hypercorn Windows signal bug
if platform.system() == "Windows":
    import multiprocessing.connection
    import _winapi
    original_wait = multiprocessing.connection._exhaustive_wait

    def safe_wait(handles, timeout):
        try:
            return original_wait(handles, timeout)
        except InterruptedError:
            return []
    multiprocessing.connection._exhaustive_wait = safe_wait
    
load_dotenv()

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LOG_DIR = (REPO_ROOT / "logs").resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)
CONTINUITY_DOCS = os.getenv("CONTINUITY_DOCS", str(REPO_ROOT / "docs" / "continuity_state.json"))
PHASE = os.getenv("PHASE", "P3 - Weighted OI + Top L/S + Alerts")

logger = logging.getLogger("futuresboard")
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logger.setLevel(getattr(logging, log_level, logging.INFO))
fh = RotatingFileHandler(str(LOG_DIR / "app.log"), maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
logger.addHandler(fh)
logger.addHandler(ch)

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    ping_timeout=30,
    ping_interval=10,
)
app = Quart(__name__)
app = cors(app, allow_origin="http://localhost:5173")
asgi_app = socketio.ASGIApp(sio, app)

from .db import (
    init_db_async,
    save_metrics_v3_async,
    get_latest_metrics_async,
    get_metrics_by_symbol_async,
    close_db_async,
    get_latest_rest_metric,
    prune_old_data_loop,
)
from .quant_engine import compute_quant_metrics, update_quant_summary, run_quant_loop
from . import ws_manager
import importlib

_rest_collector = None
try:
    _rest_collector = importlib.import_module("backend.src.futuresboard.rest_collector")
except Exception:
    try:
        _rest_collector = importlib.import_module("futuresboard.rest_collector")
    except Exception:
        _rest_collector = None

ALLOWED_TFS = ["1m", "5m", "15m", "30m", "1h"]

def to_ms(dt):
    if not dt:
        return 0
    if isinstance(dt, str):
        try:
            return int(datetime.fromisoformat(dt).timestamp() * 1000)
        except Exception:
            return 0
    if hasattr(dt, "timestamp"):
        return int(dt.timestamp() * 1000)
    return 0

def serialize_metric_row(m):
    if not m:
        return {}
    try:
        data = dict(m)
        if "updated_at" in data:
            data["timestamp"] = to_ms(data["updated_at"])
        return data
    except Exception:
        return {}

queue: asyncio.Queue = asyncio.Queue(maxsize=int(os.getenv("DB_QUEUE_MAX", "20000")))
pipeline_task: asyncio.Task | None = None

async def db_writer(q: asyncio.Queue, batch_size: int = 200, flush_interval: float = 1.0):
    from .db import save_metrics_v3_async, get_latest_rest_metric
    buffer = []
    last_flush = time.time()
    logger.info("[db_writer] started")
    while True:
        try:
            payload = await q.get()
            buffer.append(payload)
            now_ts = time.time()
            if len(buffer) >= batch_size or (now_ts - last_flush) > flush_interval:
                # We will transform queued WS payloads into metric rows by merging REST if available
                transformed = []
                for item in buffer:
                    try:
                        # If already a metrics dict (rest_collector wrote directly), keep it
                        if isinstance(item, dict) and ("symbol" in item and ("price" in item or "Price" in item or "raw" in item)):
                            sym = (item.get("symbol") or item.get("sym") or "").upper()
                            # base metric row
                            row = {
                                "symbol": sym,
                                "timeframe": "1m",
                                "price": None,
                                "funding": None,
                                "oi_usd": None,
                                "oi_abs_usd": None,
                                "global_ls_5m": None,
                                "top_ls_accounts": None,
                                "top_ls_positions": None,
                                "volume_24h": None,
                                "vol_usd": None,
                                "market_cap": None,
                                "raw_json": {"raw": item.get("raw", item)}
                            }
                            # fill from WS payload
                            # Price may be string -> convert
                            p = item.get("Price") or item.get("price") or item.get("last") or item.get("c")
                            try:
                                row["price"] = float(p) if p is not None else None
                            except Exception:
                                row["price"] = None
                            oi = item.get("openInterest") or item.get("oi") or item.get("openInterestUsd")
                            try:
                                row["oi_abs_usd"] = float(oi) if oi is not None else None
                            except Exception:
                                row["oi_abs_usd"] = None

                            # If we have a rest sample, merge it (prefer REST for funding/oi_usd/global_ls etc)
                            try:
                                rest = None
                                if sym:
                                    rest = await get_latest_rest_metric(sym)
                                if rest:
                                    # overlay rest fields where available
                                    row["funding"] = rest.get("funding_rate") if rest.get("funding_rate") is not None else row["funding"]
                                    row["oi_usd"] = rest.get("open_interest_hist_usd") if rest.get("open_interest_hist_usd") is not None else row["oi_usd"]
                                    row["global_ls_5m"] = rest.get("global_long_short_ratio")
                                    row["top_ls_accounts"] = rest.get("top_trader_account_ratio")
                                    row["top_ls_positions"] = rest.get("top_trader_long_short_ratio")
                                    # prefer rest mark price if WS price missing
                                    if row["price"] is None:
                                        row["price"] = rest.get("mark_price") or rest.get("close")
                                    # volume
                                    row["volume_24h"] = rest.get("volume") or row["volume_24h"]
                                    row["vol_usd"] = rest.get("volume") or row["vol_usd"]
                                    # attach rest raw metadata inside raw_json
                                    rawobj = row["raw_json"] or {}
                                    rawobj["rest"] = rest.get("metadata") if rest.get("metadata") else rest
                                    row["raw_json"] = rawobj
                            except Exception as e:
                                logger.debug(f"[db_writer] failed rest merge for {sym}: {e}")

                            transformed.append(row)
                        else:
                            logger.debug("[db_writer] skipping non-dict payload")
                    except Exception as e:
                        logger.warning(f"[db_writer] transform error: {e}")
                # save metrics
                if transformed:
                    try:
                        saved = await save_metrics_v3_async(transformed, timeframe="1m")
                        logger.debug(f"[db_writer] flushed {len(transformed)} merged rows (saved={saved})")
                    except Exception as e:
                        logger.warning(f"[db_writer] batch save failed: {e}")
                buffer.clear()
                last_flush = now_ts
            q.task_done()
        except asyncio.CancelledError:
            logger.info("[db_writer] cancelled — flushing remaining data")
            if buffer:
                # same logic to flush remaining
                transformed = []
                for item in buffer:
                    try:
                        if isinstance(item, dict) and ("symbol" in item):
                            sym = (item.get("symbol") or item.get("sym") or "").upper()
                            row = {"symbol": sym, "timeframe": "1m", "price": None, "funding": None, "oi_usd": None, "oi_abs_usd": None, "raw_json": {"raw": item.get("raw", item)}} 
                            p = item.get("Price") or item.get("price") or item.get("last") or item.get("c")
                            try:
                                row["price"] = float(p) if p is not None else None
                            except Exception:
                                row["price"] = None
                            oi = item.get("openInterest") or item.get("oi")
                            try:
                                row["oi_abs_usd"] = float(oi) if oi is not None else None
                            except Exception:
                                row["oi_abs_usd"] = None
                            try:
                                rest = None
                                if sym:
                                    rest = await get_latest_rest_metric(sym)
                                if rest:
                                    row["funding"] = rest.get("funding_rate")
                                    row["oi_usd"] = rest.get("open_interest_hist_usd")
                                    if row["price"] is None:
                                        row["price"] = rest.get("mark_price") or rest.get("close")
                                    rawobj = row["raw_json"] or {}
                                    rawobj["rest"] = rest.get("metadata") if rest.get("metadata") else rest
                                    row["raw_json"] = rawobj
                            except Exception:
                                pass
                            transformed.append(row)
                    except Exception:
                        pass
                if transformed:
                    try:
                        await save_metrics_v3_async(transformed, timeframe="1m")
                    except Exception as e:
                        logger.warning(f"[db_writer] final flush failed: {e}")
            break
        except Exception as e:
            logger.warning(f"[db_writer] loop error: {e}")
            await asyncio.sleep(0.5)


async def on_message_callback(payload: dict):
    """
    Normalize incoming WS messages and enqueue them for db_writer.
    payload is expected to contain symbol and some fields from ws_manager.
    """
    try:
        # symbol normalization
        sym = (payload.get("symbol") or payload.get("sym") or payload.get("s") or "").upper()
        record = {
            "symbol": sym,
            # store raw fields so db_writer can merge and map properly
            "Price": payload.get("last") or payload.get("c") or payload.get("p") or payload.get("Price"),
            "openInterest": payload.get("openInterest") or payload.get("oi"),
            "raw": payload  # keep original WS payload for debug/trace
        }
        try:
            queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.warning("[on_message] queue full — dropping payload")
    except Exception as e:
        logger.exception(f"[on_message_callback] error: {e}")


async def _start_rest_collector(symbols: list[str], poll_interval: int):
    if not _rest_collector:
        logger.info("[rest_collector] module not found — skipping rest collector start")
        return None
    fn = getattr(_rest_collector, "run", None)
    if not fn or not callable(fn):
        logger.info("[rest_collector] run() not found — skipping")
        return None
    try:
        logger.info(f"[rest_collector] starting via {_rest_collector.__name__}.run (interval={poll_interval}s)")
        task = asyncio.create_task(fn(symbols=symbols, out_queue=queue, interval=poll_interval))
        logger.info("[rest_collector] ✅ run() task created and stored (will stay alive)")
        return task
    except Exception as e:
        logger.exception(f"[rest_collector] failed to start run(): {e}")
        return None


async def quant_update_loop(interval: int = 60):
    logger.info(f"[QuantLoop] starting (interval={interval}s)")
    while True:
        try:
            try:
                inserted = await update_quant_summary()
                logger.debug(f"[QuantLoop] update_quant_summary inserted={inserted}")
            except Exception as e:
                logger.debug(f"[QuantLoop] update_quant_summary failed: {e}")

            try:
                computed = await compute_quant_metrics(limit=200)
                if computed:
                    def _safe_json(data):
                        def default(o):
                            if isinstance(o, datetime):
                                return o.isoformat()
                            return str(o)
                        return json.loads(json.dumps(data, default=default))
                    await sio.emit("quant_update", {"data": _safe_json(computed), "ts": datetime.utcnow().isoformat()})
                    logger.info(f"[QuantLoop] emitted quant_update ({len(computed)} rows)")
            except Exception as e:
                logger.debug(f"[QuantLoop] compute/emit failed: {e}")

        except asyncio.CancelledError:
            logger.info("[QuantLoop] cancelled")
            raise
        except Exception as e:
            logger.warning(f"[QuantLoop] loop error: {e}")

        await asyncio.sleep(interval)


bg_tasks: list[asyncio.Task] = []
ws_started: bool = False
pipeline_task: asyncio.Task | None = None


@app.before_serving
async def startup():
    global pipeline_task, bg_tasks, ws_started
    logger.info("[Lifecycle] before_serving startup – creating tasks")
    try:
        await init_db_async()
    except Exception as e:
        logger.exception(f"[Lifecycle] init_db_async failed: {e}")
        raise

    # start db_writer
    if pipeline_task is None or (pipeline_task and pipeline_task.done()):
        pipeline_task = asyncio.create_task(db_writer(queue))
        logger.info("[Pipeline] DB writer started")

    # symbols
    symbols_env = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
    symbols = [s.strip().upper() for s in symbols_env.split(",") if s.strip()]

    # ws manager
    try:
        ws_poll_symbols = [s.lower() for s in symbols]
        await ws_manager.start_all(ws_poll_symbols, on_message_callback)
        ws_started = True
        logger.info("[Exchange] WS manager started (%d symbols)", len(ws_poll_symbols))
    except Exception as e:
        logger.exception(f"[Exchange] ws_manager.start_all failed: {e}")

    # slight delay to allow event loop to stabilize (works across uvicorn/hypercorn)
    await asyncio.sleep(0.05)
    # start rest collector
    rest_interval = int(os.getenv("REST_POLL_INTERVAL", "10"))
    rest_task = await _start_rest_collector(symbols, rest_interval)
    if rest_task:
        bg_tasks.append(rest_task)

    # quant loop
        # start HF quant loop (5s cadence) using quant_engine.run_quant_loop
    try:
        hf_interval = float(os.getenv("QUANT_5S_INTERVAL", "5.0"))
        qt = asyncio.create_task(run_quant_loop(hf_interval))
        bg_tasks.append(qt)
        logger.info("[QuantLoop] 5s quant loop started (interval=%ss)", hf_interval)
    except Exception as e:
        logger.exception(f"[QuantLoop] failed to start 5s loop: {e}")

    # start pruning background loop (separate safe task)
    try:
        prune_interval_hours = int(os.getenv("PRUNE_INTERVAL_HOURS", "6"))
        prune_days = int(os.getenv("PRUNE_DAYS", "60"))
        prune_task = asyncio.create_task(db.prune_old_data_loop(interval_hours=prune_interval_hours, days=prune_days))
        bg_tasks.append(prune_task)
        logger.info("[PruneLoop] prune_old_data_loop started (every %s hours, keep %s days)", prune_interval_hours, prune_days)
    except Exception as e:
        logger.exception(f"[PruneLoop] failed to start: {e}")

    # start diagnostics loop (every 60 s)
    try:
        diag_interval = int(os.getenv("DIAGNOSTICS_INTERVAL", "60"))
        diag_task = asyncio.create_task(diagnostics_loop(diag_interval))
        bg_tasks.append(diag_task)
        logger.info("[Diagnostics] diagnostics_loop started (interval=%ss)", diag_interval)
    except Exception as e:
        logger.exception(f"[Diagnostics] failed to start: {e}")

    # start signal families loop
    try:
        signals_interval = int(os.getenv("SIGNALS_INTERVAL", "60"))
        sig_task = asyncio.create_task(signals_loop(signals_interval))
        bg_tasks.append(sig_task)
        logger.info("[Signals] signals_loop started (interval=%ss)", signals_interval)
    except Exception as e:
        logger.exception(f"[Signals] failed to start: {e}")

    # start confluence loop
    try:
        conf_interval = int(os.getenv("CONFLUENCE_INTERVAL", "60"))
        conf_task = asyncio.create_task(confluence_loop(conf_interval))
        bg_tasks.append(conf_task)
        logger.info("[Confluence] confluence_loop started (interval=%ss)", conf_interval)
    except Exception as e:
        logger.exception(f"[Confluence] failed to start: {e}")

        # continuity loop
    asyncio.create_task(_continuity_sync_loop_safe())

    logger.info("[Lifecycle] startup complete ✅")


@app.after_serving
async def shutdown():
    global pipeline_task, bg_tasks, ws_started
    logger.info("[Lifecycle] after_serving shutdown – cleaning tasks")
    try:
        if ws_started:
            await ws_manager.stop_all()
            logger.info("[Exchange] WS manager stopped")
    except Exception as e:
        logger.warning(f"[Lifecycle] stop ws_manager failed: {e}")

    for t in bg_tasks:
        if t and not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
    bg_tasks.clear()

    if pipeline_task:
        pipeline_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pipeline_task
        logger.info("[Pipeline] DB writer stopped")

    await close_db_async()
    logger.info("Shutdown complete.")


async def _continuity_sync_loop_safe():
    start = time.time()
    while True:
        try:
            state_path = pathlib.Path(CONTINUITY_DOCS)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            uptime = round(time.time() - start, 2)
            state = {"timestamp": datetime.utcnow().isoformat(timespec="seconds"), "uptime": uptime, "phase": PHASE}
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Continuity] heartbeat failed: {e}")
        await asyncio.sleep(300)


@app.route("/health")
async def health():
    state = {}
    try:
        if os.path.exists(CONTINUITY_DOCS):
            with open(CONTINUITY_DOCS, "r", encoding="utf-8") as f:
                state = json.load(f)
    except Exception as e:
        logger.warning(f"Failed reading continuity state: {e}")
    resp = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        "version": "v0.4.0",
        "continuity": {
            "phase": state.get("phase", PHASE),
            "uptimePct": state.get("uptimePct", 100),
            "backend": state.get("backend", "unknown"),
            "last_sync": state.get("timestamp", "N/A"),
        },
    }
    return jsonify(resp)


@app.route("/api/metrics/history")
async def api_metrics_history():
    limit = request.args.get("limit", 100, type=int)
    tf = request.args.get("tf")
    symbol = request.args.get("symbol")
    try:
        rows = await get_latest_metrics_async(limit=limit, tf=tf, symbol=symbol)
        return jsonify([serialize_metric_row(m) for m in rows])
    except Exception as e:
        logger.warning(f"[API] /metrics/history failed: {e}")
        return jsonify([]), 500


@app.route("/api/metrics/<symbol>/history")
async def api_symbol_history(symbol):
    tf = request.args.get("tf")
    if tf and tf not in ALLOWED_TFS:
        return jsonify({"error": "Invalid timeframe"}), 400
    limit = request.args.get("limit", 100, type=int)
    try:
        rows = await get_metrics_by_symbol_async(symbol, limit=limit, tf=tf)
        return jsonify([serialize_metric_row(m) for m in rows])
    except Exception as e:
        logger.warning(f"[API] /metrics/{symbol}/history failed: {e}")
        return jsonify([]), 500


@app.route("/api/quant/summary")
async def api_quant_summary():
    limit = request.args.get("limit", 100, type=int)
    try:
        computed = await compute_quant_metrics(limit=limit)
        return jsonify({"status": "computed", "data": computed})
    except Exception as e:
        logger.warning(f"[API] /api/quant/summary failed: {e}")
        return jsonify({"status": "error", "data": []}), 500


@app.route("/api/metrics")
async def api_metrics():
    return redirect("/api/metrics/history")


@app.route("/favicon.ico")
async def favicon():
    return "", 204

@app.route("/api/signals/latest")
async def api_signals_latest():
    try:
        conn = await db._pool.acquire()
        rows = await conn.fetch("""
            SELECT DISTINCT ON (symbol, family)
                symbol, family, score, confidence, ts
            FROM quant_signals
            ORDER BY symbol, family, ts DESC
        """)
        await db._pool.release(conn)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/signals/latest failed: {e}")
        return jsonify([]), 500

@app.route("/api/confluence/latest")
async def api_confluence_latest():
    try:
        conn = await db._pool.acquire()
        rows = await conn.fetch("""
            SELECT DISTINCT ON (symbol)
                symbol, ts, confluence_score, bull_strength, bear_strength, volatility
            FROM quant_confluence
            ORDER BY symbol, ts DESC
        """)
        await db._pool.release(conn)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/confluence/latest failed: {e}")
        return jsonify([]), 500


@sio.event
async def connect(sid, environ):
    logger.info(f"[SocketIO] Client connected: {sid}")


@sio.event
async def disconnect(sid):
    logger.info(f"[SocketIO] Client disconnected: {sid}")


def main():
    parser = argparse.ArgumentParser(description="Crypto Futures Dashboard (Quart backend)")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")))
    args = parser.parse_args()

    port = args.port
    loop = asyncio.get_event_loop()
    # avoid messing with signals when running under hypercorn/uvicorn import-based ASGI
    if __name__ == "__main__":
        signal.signal(signal.SIGINT, lambda s, f: loop.stop())
        signal.signal(signal.SIGTERM, lambda s, f: loop.stop())

    logger.info(f"Starting Quart+SocketIO ASGI server on 0.0.0.0:{port}")
    import hypercorn.asyncio
    from hypercorn.config import Config

    config = Config()
    config.bind = [f"0.0.0.0:{port}"]
    asyncio.run(hypercorn.asyncio.serve(asgi_app, config))


if __name__ == "__main__":
    main()

# export asgi_app for uvicorn/hypercorn
app = asgi_app
