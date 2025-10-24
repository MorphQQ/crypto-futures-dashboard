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
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# Project imports (config first to apply log level early)
from .config import get_settings, reload_settings
cfg = get_settings()

# -------------------------
# Unified Logging Init (use cfg.LOG_LEVEL)
# -------------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LOG_DIR = (REPO_ROOT / "logs").resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("futuresboard")
# Clear existing handlers to avoid duplicates when reloading/configuring
for h in list(logger.handlers):
    logger.removeHandler(h)

log_level_name = getattr(cfg, "LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_name, logging.INFO)
logger.setLevel(log_level)

fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
fh = RotatingFileHandler(str(LOG_DIR / "app.log"), maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
fh.setFormatter(fmt)
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(fmt)
logger.addHandler(fh)
logger.addHandler(ch)
# also set root logger level for libraries
logging.getLogger().setLevel(log_level)

# -------------------------
# Safe task cancel helper
# -------------------------
async def cancel_all(tasks: list[asyncio.Task]):
    """
    Cancel tasks and await them safely.
    Use: await cancel_all(bg_tasks + ([pipeline_task] if pipeline_task else []))
    """
    if not tasks:
        return
    for t in tasks:
        try:
            if t and not t.done():
                t.cancel()
        except Exception:
            pass
    # await results, ignore CancelledError
    results = await asyncio.gather(*[t for t in tasks if t], return_exceptions=True)
    for r in results:
        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
            logger.debug(f"[cancel_all] task ended with: {type(r).__name__}: {r}")


# -------------------------
# Safe loop cancellation template
# -------------------------
async def safe_loop_template(name: str, loop_coro: callable, interval: float = 5.0, flush_coro: callable | None = None):
    """
    Generic wrapper to run `loop_coro()` repeatedly with graceful cancellation and optional flush on exit.
    `loop_coro` must be an async callable implementing a single iteration of work.
    """
    logger.info(f"[{name}] started (interval={interval}s)")
    try:
        while True:
            try:
                await loop_coro()
            except asyncio.CancelledError:
                logger.info(f"[{name}] cancelled during iteration — will flush and exit")
                raise
            except Exception as e:
                logger.warning(f"[{name}] iteration error: {type(e).__name__}: {e}")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info(f"[{name}] cancelled — running flush/cleanup (if provided)")
        if flush_coro:
            try:
                await flush_coro()
                logger.info(f"[{name}] flush complete on cancel")
            except Exception as e:
                logger.warning(f"[{name}] flush on cancel failed: {e}")
        return
    except Exception as e:
        logger.error(f"[{name}] critical loop failure: {type(e).__name__}: {e}")
        if flush_coro:
            try:
                await flush_coro()
            except Exception:
                pass
        return

# -------------------------
# Safe async runner wrapper
# -------------------------
def safe_loop_runner(coro):
    """
    Wrap a coroutine into a managed asyncio.Task that ignores CancelledError.
    Ensures WS and background tasks are gracefully stoppable.
    """
    async def _wrapper():
        try:
            await coro
        except asyncio.CancelledError:
            logger.info("[safe_loop_runner] task cancelled gracefully")
        except Exception as e:
            logger.warning(f"[safe_loop_runner] unhandled error: {e}")
    return asyncio.create_task(_wrapper())

# -------------------------
# ASGI/App initialization
# -------------------------
from quart import Quart, jsonify, request, redirect
from quart_cors import cors
import socketio

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    ping_timeout=30,
    ping_interval=10,
)
_quart_app = Quart(__name__)
_quart_app = cors(_quart_app, allow_origin="http://localhost:5173")
asgi_app = socketio.ASGIApp(sio, _quart_app)
# keep a reference named `app` for imports that expect it; final export will set app = asgi_app
app = _quart_app

# Patch Hypercorn Windows signal bug
if platform.system() == "Windows":
    try:
        import multiprocessing.connection
        import _winapi
        original_wait = multiprocessing.connection._exhaustive_wait

        def safe_wait(handles, timeout):
            try:
                return original_wait(handles, timeout)
            except InterruptedError:
                return []
        multiprocessing.connection._exhaustive_wait = safe_wait
    except Exception:
        # best-effort patch; continue if not available
        pass

load_dotenv()

CONTINUITY_DOCS = os.getenv("CONTINUITY_DOCS", str(REPO_ROOT / "docs" / "continuity_state.json"))
PHASE = getattr(cfg, "PHASE", os.getenv("PHASE", "P3 - Weighted OI + Top L/S + Alerts"))

# -------------------------
# DB / Quant / WS imports (use internal names)
# -------------------------
from . import db
from .db import (
    init_db_async,
    save_metrics_v3_async,
    get_latest_metrics_async,
    get_metrics_by_symbol_async,
    close_db_async,
    get_latest_rest_metric,
    prune_old_data_loop,
)
# import quant loops/defs
from .quant_engine import (
    compute_quant_metrics,
    update_quant_summary,
    run_quant_loop,
    diagnostics_loop,
    signals_loop,
    confluence_loop,
    context_scoring_loop,
    context_trends_loop as quant_context_trends_loop,
    regime_loop,
)
from . import ws_manager
import importlib

# Rest collector dynamic import (keeps original behavior)
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

# -------------------------
# Queue + task tracking
# -------------------------
queue: asyncio.Queue = asyncio.Queue(maxsize=int(os.getenv("DB_QUEUE_MAX", "20000")))
bg_tasks: list[asyncio.Task] = []
pipeline_task: asyncio.Task | None = None

# -------------------------
# db_writer implementation (safe + offload heavy transforms)
# -------------------------
async def db_writer_worker(buffer: list, timeframe: str = "1m"):
    """
    Transform buffer (list of dict WS/rest payloads) into rows and call save_metrics_v3_async.
    Offloads heavy CPU work to thread via asyncio.to_thread.
    """
    def transform_sync(buffer_snapshot):
        transformed = []
        for item in buffer_snapshot:
            try:
                if isinstance(item, dict) and ("symbol" in item or item.get("sym")):
                    sym = (item.get("symbol") or item.get("sym") or "").upper()
                    row = {
                        "symbol": sym,
                        "timeframe": timeframe,
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
                    transformed.append((row, item))  # attach original for potential REST overlay
            except Exception:
                # skip malformed
                continue
        # We return transformed list of (row, original) so further async operations can merge rest data
        return transformed

    try:
        # snapshot buffer for thread transform
        buffer_snapshot = list(buffer)
        transformed_pairs = await asyncio.to_thread(transform_sync, buffer_snapshot)
        # Now merge REST samples asynchronously where available
        transformed_rows = []
        for row, orig in transformed_pairs:
            try:
                sym = row.get("symbol")
                if sym:
                    rest = await get_latest_rest_metric(sym)
                else:
                    rest = None
                if rest:
                    row["funding"] = rest.get("funding_rate") if rest.get("funding_rate") is not None else row.get("funding")
                    row["oi_usd"] = rest.get("open_interest_hist_usd") if rest.get("open_interest_hist_usd") is not None else row.get("oi_usd")
                    row["global_ls_5m"] = rest.get("global_long_short_ratio") or row.get("global_ls_5m")
                    row["top_ls_accounts"] = rest.get("top_trader_account_ratio") or row.get("top_ls_accounts")
                    row["top_ls_positions"] = rest.get("top_trader_long_short_ratio") or row.get("top_ls_positions")
                    if row.get("price") is None:
                        row["price"] = rest.get("mark_price") or rest.get("close") or row.get("price")
                    row["volume_24h"] = rest.get("volume") or row.get("volume_24h")
                    row["vol_usd"] = rest.get("volume") or row.get("vol_usd")
                    rawobj = row.get("raw_json") or {}
                    rawobj["rest"] = rest.get("metadata") if rest.get("metadata") else rest
                    row["raw_json"] = rawobj
                transformed_rows.append(row)
            except Exception as e:
                logger.debug(f"[db_writer_worker] merge/rest error for {row.get('symbol')}: {e}")
                transformed_rows.append(row)
        if transformed_rows:
            saved = await save_metrics_v3_async(transformed_rows, timeframe=timeframe)
            logger.debug(f"[db_writer_worker] saved {len(transformed_rows)} rows (save_metrics returned: {saved})")
            return saved
        return 0
    except Exception as e:
        logger.warning(f"[db_writer_worker] failed: {e}")
        return 0

async def db_writer(q: asyncio.Queue, batch_size: int = 200, flush_interval: float = 1.0):
    buffer: list = []
    last_flush = time.time()
    logger.info("[db_writer] started")
    closed = False

    async def loop_iteration():
        nonlocal last_flush, buffer
        payload = await q.get()
        buffer.append(payload)
        now_ts = time.time()
        if len(buffer) >= batch_size or (now_ts - last_flush) > flush_interval:
            to_flush = list(buffer)
            buffer.clear()
            last_flush = now_ts
            try:
                await db_writer_worker(to_flush, timeframe="1m")
                logger.debug(f"[db_writer] flushed {len(to_flush)} rows")
            except Exception as e:
                logger.warning(f"[db_writer] batch save failed: {e}")
        # mark q.task_done after processing this payload
        try:
            q.task_done()
        except Exception:
            pass

    async def flush_remaining():
        nonlocal buffer
        if buffer:
            try:
                await db_writer_worker(buffer, timeframe="1m")
            except Exception as e:
                logger.warning(f"[db_writer] final flush failed: {e}")
            buffer.clear()

    # Run the safe loop wrapper
    await safe_loop_template("db_writer", loop_coro=loop_iteration, interval=0.01, flush_coro=flush_remaining)


# -------------------------
# on_message normalizer
# -------------------------
async def on_message_callback(payload: dict):
    """
    Normalize incoming WS messages and enqueue them for db_writer.
    payload is expected to contain symbol and some fields from ws_manager.
    """
    try:
        sym = (payload.get("symbol") or payload.get("sym") or payload.get("s") or "").upper()
        record = {
            "symbol": sym,
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


# -------------------------
# rest_collector starter
# -------------------------
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
        logger.info("[rest_collector] ✓ run() task created and stored (will stay alive)")
        return task
    except Exception as e:
        logger.exception(f"[rest_collector] failed to start run(): {e}")
        return None


# -------------------------
# Quant update loop (wrap compute & emit as single iteration)
# -------------------------
async def quant_update_iteration():
    """
    Single iteration used by safe_loop_template. Emits quant_update via socket when available.
    """
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
    except Exception as e:
        logger.warning(f"[QuantLoop] iteration failed: {e}")


# -------------------------
# Regime transition monitor (single iteration)
# -------------------------
async def regime_transition_iteration():
    try:
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (symbol)
                    symbol, regime, confidence, ts
                FROM quant_regimes
                ORDER BY symbol, ts DESC
            """)
        for r in rows:
            sym, regime = r["symbol"], r["regime"]
            # last_seen state stored on closure object in loop wrapper (safe_loop_template does not keep state)
            # We'll attach last_seen to the function object for persistence between iterations.
            prev = getattr(regime_transition_iteration, "last_seen", {}).get(sym)
            if prev and regime != prev:
                logger.info(f"[RegimeTransition] {sym}: {prev} → {regime}")
                try:
                    await sio.emit("regime_transition", {
                        "symbol": sym,
                        "from": prev,
                        "to": regime,
                        "confidence": r.get("confidence"),
                        "ts": r.get("ts").isoformat() if r.get("ts") else datetime.utcnow().isoformat()
                    })
                except Exception as e:
                    logger.debug(f"[RegimeTransition] emit failed: {e}")
            if not hasattr(regime_transition_iteration, "last_seen"):
                regime_transition_iteration.last_seen = {}
            regime_transition_iteration.last_seen[sym] = regime
    except Exception as e:
        logger.warning(f"[RegimeTransitions] iteration failed: {e}")


# -------------------------
# Context trends loop (single iteration)
# -------------------------
async def context_trends_iteration():
    try:
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (symbol) symbol, bias, context_score, ts
                FROM quant_context_scores
                ORDER BY symbol, ts DESC
            """)
        transitions = []
        now = datetime.now(timezone.utc)
        prev_biases = getattr(context_trends_iteration, "prev_biases", {})
        for r in rows:
            sym = r["symbol"]
            curr_bias = (r["bias"] or "neutral").lower()
            prev_bias = prev_biases.get(sym)
            if prev_bias and prev_bias != curr_bias:
                transitions.append({
                    "symbol": sym,
                    "ts": now,
                    "from_bias": prev_bias,
                    "to_bias": curr_bias,
                    "context_score": float(r.get("context_score") or 0),
                    "raw_json": {
                        "prev": prev_bias,
                        "curr": curr_bias,
                        "ts_ref": str(r.get("ts"))
                    }
                })
            prev_biases[sym] = curr_bias
        context_trends_iteration.prev_biases = prev_biases
        if transitions:
            try:
                await db.save_quant_context_trends_async(transitions)
                logger.info(f"[ContextTrends] detected {len(transitions)} transitions")
            except Exception as e:
                logger.warning(f"[ContextTrends] save failed: {e}")
    except Exception as e:
        logger.warning(f"[ContextTrends] loop failed: {e}")


# -------------------------
# Continuity heartbeat (single iteration)
# -------------------------
async def continuity_heartbeat_iteration():
    try:
        state_path = pathlib.Path(CONTINUITY_DOCS)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        uptime = round(time.time() - getattr(continuity_heartbeat_iteration, "start", time.time()), 2)
        state = {"timestamp": datetime.utcnow().isoformat(timespec="seconds"), "uptime": uptime, "phase": PHASE}
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"[Continuity] heartbeat failed: {e}")
continuity_heartbeat_iteration.start = time.time()


# -------------------------
# Lifecycle handlers (startup/shutdown)
# -------------------------
ws_started: bool = False

@app.before_serving
async def startup():
    global pipeline_task, bg_tasks, ws_started
    logger.info("[Lifecycle] before_serving startup – creating tasks")
    try:
        await init_db_async()
    except Exception as e:
        logger.exception(f"[Lifecycle] init_db_async failed: {e}")
        raise

    # start db_writer pipeline task if missing
    if pipeline_task is None or (pipeline_task and pipeline_task.done()):
        pipeline_task = asyncio.create_task(db_writer(queue))
        logger.info("[Pipeline] DB writer started")

    # symbols (supports both list[str] and comma-separated string)
    symbols_env = getattr(cfg, "SYMBOLS", None)
    if isinstance(symbols_env, list):
        symbols = [s.strip().upper() for s in symbols_env if s and isinstance(s, str)]
    else:
        raw_symbols = symbols_env or os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
        symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]


    # ws manager
    try:
        ws_poll_symbols = [s.lower() for s in symbols]
        safe_loop_runner(ws_manager.start_all(ws_poll_symbols, on_message_callback))
        ws_started = True
        logger.info("[Exchange] WS manager started safely (%d symbols)", len(ws_poll_symbols))
    except Exception as e:
        logger.exception(f"[Exchange] ws_manager.start_all failed: {e}")

    # small stabilization delay
    await asyncio.sleep(0.05)

    # start rest collector
    rest_interval = int(os.getenv("REST_POLL_INTERVAL", "10"))
    rest_task = await _start_rest_collector(symbols, rest_interval)
    if rest_task:
        bg_tasks.append(rest_task)

    # start HF quant loop (5s cadence) using quant_engine.run_quant_loop
    try:
        hf_interval = float(os.getenv("QUANT_5S_INTERVAL", "5.0"))
        # run_quant_loop is assumed to be a coroutine that internally loops; we run it as a background task
        qt = asyncio.create_task(run_quant_loop(hf_interval))
        bg_tasks.append(qt)
        logger.info("[QuantLoop] 5s quant loop started (interval=%ss)", hf_interval)
    except Exception as e:
        logger.exception(f"[QuantLoop] failed to start 5s loop: {e}")

    # start pruning background loop (separate safe task)
    try:
        prune_interval_hours = int(os.getenv("PRUNE_INTERVAL_HOURS", "6"))
        prune_days = int(os.getenv("PRUNE_DAYS", "60"))
        prune_task = asyncio.create_task(prune_old_data_loop(interval_hours=prune_interval_hours, days=prune_days))
        bg_tasks.append(prune_task)
        logger.info("[PruneLoop] prune_old_data_loop started (every %s hours, keep %s days)", prune_interval_hours, prune_days)
    except Exception as e:
        logger.exception(f"[PruneLoop] failed to start: {e}")

    # start diagnostics loop (every 60 s) - using provided diagnostics_loop if it loops internally, else wrap
    try:
        diag_interval = int(os.getenv("DIAGNOSTICS_INTERVAL", "60"))
        # diagnostics_loop may be a coroutine loop; if it is a single-iteration coroutine, we wrap accordingly.
        diag_task = asyncio.create_task(diagnostics_loop(diag_interval))
        bg_tasks.append(diag_task)
        logger.info("[Diagnostics] diagnostics_loop started (interval=%ss)", diag_interval)
    except Exception as e:
        logger.exception(f"[Diagnostics] failed to start: {e}")

    # signals loop
    try:
        signals_interval = int(os.getenv("SIGNALS_INTERVAL", "60"))
        sig_task = asyncio.create_task(signals_loop(signals_interval))
        bg_tasks.append(sig_task)
        logger.info("[Signals] signals_loop started (interval=%ss)", signals_interval)
    except Exception as e:
        logger.exception(f"[Signals] failed to start: {e}")

    # confluence loop
    try:
        conf_interval = int(os.getenv("CONFLUENCE_INTERVAL", "60"))
        conf_task = asyncio.create_task(confluence_loop(conf_interval))
        bg_tasks.append(conf_task)
        logger.info("[Confluence] confluence_loop started (interval=%ss)", conf_interval)
    except Exception as e:
        logger.exception(f"[Confluence] failed to start: {e}")

    # regime loop
    try:
        regime_interval = int(os.getenv("REGIME_INTERVAL", "300"))
        regime_task = asyncio.create_task(regime_loop(regime_interval))
        bg_tasks.append(regime_task)
        logger.info("[Regime] regime_loop started (interval=%ss)", regime_interval)
    except Exception as e:
        logger.exception(f"[Regime] failed to start: {e}")

    # regime transition monitor (safe wrapper)
    try:
        transition_interval = int(os.getenv("REGIME_TRANSITION_INTERVAL", "120"))
        transition_task = asyncio.create_task(safe_loop_template("RegimeTransitions", regime_transition_iteration, interval=transition_interval))
        bg_tasks.append(transition_task)
        logger.info("[RegimeTransitions] monitor started (interval=%ss)", transition_interval)
    except Exception as e:
        logger.exception(f"[RegimeTransitions] failed to start: {e}")

    # context scoring loop
    try:
        ctx_interval = int(os.getenv("CONTEXT_SCORING_INTERVAL", "60"))
        ctx_task = asyncio.create_task(context_scoring_loop(ctx_interval))
        bg_tasks.append(ctx_task)
        logger.info("[ContextScoring] context_scoring_loop started (interval=%ss)", ctx_interval)
    except Exception as e:
        logger.exception(f"[ContextScoring] failed to start: {e}")

    # context trends loop (wrapped)
    try:
        ctx_trend_interval = float(os.getenv("CONTEXT_TRENDS_INTERVAL", "120.0"))
        ctx_trend_task = asyncio.create_task(safe_loop_template("ContextTrends", context_trends_iteration, interval=ctx_trend_interval))
        bg_tasks.append(ctx_trend_task)
        logger.info("[ContextTrends] monitor started (interval=%ss)", ctx_trend_interval)
    except Exception as e:
        logger.exception(f"[ContextTrends] failed to start: {e}")

    # continuity heartbeat - keep tracked so it cancels cleanly
    try:
        heartbeat_interval = int(os.getenv("CONTINUITY_HEARTBEAT_INTERVAL", "300"))
        heartbeat_task = asyncio.create_task(safe_loop_template("ContinuityHeartbeat", continuity_heartbeat_iteration, interval=heartbeat_interval))
        bg_tasks.append(heartbeat_task)
    except Exception as e:
        logger.exception(f"[Continuity] failed to start heartbeat: {e}")

    logger.info("[Lifecycle] startup complete ✓")


@app.after_serving
async def shutdown():
    """
    Graceful backend shutdown:
    - Stop WebSocket manager safely
    - Write continuity snapshot
    - Cancel background tasks
    - Close DB connection
    """
    global pipeline_task, bg_tasks, ws_started
    logger.info("[Lifecycle] after_serving shutdown – cleaning tasks")

    # --- Continuity snapshot ---
    try:
        snapshot = {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
            "phase": PHASE,
            "status": "shutdown",
            "ws_active": ws_started,
            "bg_tasks": len(bg_tasks),
        }
        snap_path = pathlib.Path(CONTINUITY_DOCS)
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        logger.info("[Continuity] snapshot written at shutdown")
    except Exception as e:
        logger.debug(f"[Continuity] snapshot write failed: {e}")

    # --- Stop WebSocket Manager ---
    if ws_started:
        try:
            await ws_manager.stop_all()
            logger.info("[Exchange] WS manager stopped")
        except Exception as e:
            logger.warning(f"[Lifecycle] stop ws_manager failed: {e}")

    # --- Cancel Background Tasks ---
    try:
        tracked = list(bg_tasks) + ([pipeline_task] if pipeline_task else [])
        await cancel_all(tracked)
        bg_tasks.clear()
        logger.info("[Pipeline] background tasks cancelled")
    except Exception as e:
        logger.warning(f"[Lifecycle] cancel_all failed: {e}")

    # --- Close DB Connection ---
    try:
        await close_db_async()
        logger.info("[Lifecycle] DB connection closed")
    except Exception as e:
        logger.warning(f"[Lifecycle] close_db_async failed: {e}")

    logger.info("Shutdown complete.")



# -------------------------
# API endpoints
# -------------------------
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
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (symbol, family)
                    symbol, family, score, confidence, ts
                FROM quant_signals
                ORDER BY symbol, family, ts DESC
            """)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/signals/latest failed: {e}")
        return jsonify([]), 500


@app.route("/api/confluence/latest")
async def api_confluence_latest():
    try:
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (symbol)
                    symbol, ts, confluence_score, bull_strength, bear_strength, volatility
                FROM quant_confluence
                ORDER BY symbol, ts DESC
            """)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/confluence/latest failed: {e}")
        return jsonify([]), 500


@app.route("/api/diagnostics/latest")
async def api_diagnostics_latest():
    try:
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (symbol)
                    symbol,
                    ts,
                    COALESCE(volatility_5s, 0) AS volatility_5s,
                    COALESCE(volatility_zscore, 0) AS volatility_zscore,
                    COALESCE(corr_price_oi, 0) AS corr_price_oi,
                    COALESCE(corr_price_ls, 0) AS corr_price_ls,
                    COALESCE(corr_oi_ls, 0) AS corr_oi_ls,
                    COALESCE(confluence_density, 0) AS confluence_density
                FROM quant_diagnostics
                ORDER BY symbol, ts DESC
            """)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/diagnostics/latest failed: {e}")
        return jsonify([]), 500


@app.route("/api/regime/latest")
async def api_regime_latest():
    try:
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (symbol)
                    symbol, ts, regime, confidence, confluence_score, volatility
                FROM quant_regimes
                ORDER BY symbol, ts DESC
            """)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/regime/latest failed: {e}")
        return jsonify([]), 500


@app.route("/api/regime/history")
async def api_regime_history():
    try:
        symbol = request.args.get("symbol", "BTCUSDT")
        limit = request.args.get("limit", 200, type=int)
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT symbol, ts, regime, confidence, confluence_score, volatility
                FROM quant_regimes
                WHERE symbol = $1
                ORDER BY ts DESC
                LIMIT $2
            """, symbol, limit)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/regime/history failed: {e}")
        return jsonify([]), 500


@app.route("/api/regime/transitions/latest")
async def api_regime_transitions_latest():
    try:
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                WITH recent AS (
                    SELECT symbol, regime, ts,
                        LAG(regime) OVER (PARTITION BY symbol ORDER BY ts) AS prev_regime
                    FROM quant_regimes
                    WHERE ts > now() - interval '1 day'
                )
                SELECT symbol, prev_regime AS from, regime AS to, ts
                FROM recent
                WHERE prev_regime IS NOT NULL AND regime <> prev_regime
                ORDER BY ts DESC
                LIMIT 100
            """)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/regime/transitions/latest failed: {e}")
        return jsonify([]), 500


@app.route("/api/context/summary")
async def api_context_summary():
    try:
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (symbol)
                    symbol, ts, context_score, bias, components
                FROM quant_context_scores
                ORDER BY symbol, ts DESC
            """)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/context/summary failed: {e}")
        return jsonify([]), 500


@app.route("/api/context/trends")
async def api_context_trends():
    try:
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT symbol, from_bias, to_bias, ts
                FROM quant_context_trends
                ORDER BY ts DESC
                LIMIT 50
            """)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"[API] /api/context/trends failed: {e}")
        return jsonify([]), 500


@app.route("/api/context/latest")
async def api_context_latest():
    """
    Return the latest context scoring snapshot for each symbol,
    merged with regime + confluence if available.
    """
    try:
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("""
                WITH latest_ctx AS (
                    SELECT DISTINCT ON (symbol)
                        symbol, ts, context_score, bias, components
                    FROM quant_context_scores
                    ORDER BY symbol, ts DESC
                ),
                latest_reg AS (
                    SELECT DISTINCT ON (symbol)
                        symbol, regime, confidence AS regime_conf
                    FROM quant_regimes
                    ORDER BY symbol, ts DESC
                ),
                latest_conf AS (
                    SELECT DISTINCT ON (symbol)
                        symbol, confluence_score, bull_strength, bear_strength
                    FROM quant_confluence
                    ORDER BY symbol, ts DESC
                )
                SELECT 
                    c.symbol,
                    c.ts,
                    c.context_score,
                    c.bias,
                    c.components,
                    r.regime,
                    r.regime_conf,
                    f.confluence_score,
                    f.bull_strength,
                    f.bear_strength
                FROM latest_ctx c
                LEFT JOIN latest_reg r USING(symbol)
                LEFT JOIN latest_conf f USING(symbol)
                ORDER BY c.context_score DESC
            """)
        out = []
        for r in rows:
            d = dict(r)
            try:
                if isinstance(d.get("components"), str):
                    d["components"] = json.loads(d["components"])
            except Exception:
                pass
            out.append(d)
        return jsonify(out)
    except Exception as e:
        logger.warning(f"[API] /api/context/latest failed: {e}")
        return jsonify([]), 500


# -------------------------
# System control route — config reload
# -------------------------
@app.route("/api/system/reload-config", methods=["POST"])
async def api_reload_config():
    """
    Hot-reload the .env file and update runtime configuration.
    This lets you change settings (symbols, log level, etc.)
    without restarting the backend.
    """
    try:
        # Re-load configuration from .env via pydantic
        new_cfg = reload_settings()

        # Update logging level immediately across all handlers
        try:
            new_level = getattr(logging, new_cfg.LOG_LEVEL.upper(), logging.INFO)
            root_logger = logging.getLogger()
            root_logger.setLevel(new_level)
            for h in root_logger.handlers:
                h.setLevel(new_level)
            # also set our module logger
            logger.setLevel(new_level)
            logger.info(f"[Reload-Config] Applied new LOG_LEVEL: {new_cfg.LOG_LEVEL.upper()}")
        except Exception as log_err:
            logger.warning(f"[Reload-Config] Log level update failed: {log_err}")

        response = {
            "status": "ok",
            "message": "Configuration reloaded successfully.",
            "symbols": getattr(new_cfg, "SYMBOLS", None),
            "phase": getattr(new_cfg, "PHASE", None),
            "log_level": new_cfg.LOG_LEVEL.upper(),
            "auto_scrape_interval": getattr(new_cfg, "AUTO_SCRAPE_INTERVAL", None),
            "db_host": new_cfg.DATABASE_URL.split("@")[-1] if getattr(new_cfg, "DATABASE_URL", None) and "@" in new_cfg.DATABASE_URL else getattr(new_cfg, "DATABASE_URL", None),
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        }

        return jsonify(response), 200

    except Exception as e:
        logger.exception("[API] reload-config failed")
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


@app.route("/api/db/status", methods=["GET"])
async def api_db_status():
    stats = await db.get_pool_stats()
    return jsonify(stats)

@app.route("/api/system/continuity", methods=["GET"])
async def api_system_continuity():
    """
    Report live backend continuity status (WS tasks, DB queue size, uptime).
    """
    try:
        uptime = round(time.time() - continuity_heartbeat_iteration.start, 2)
        return jsonify({
            "phase": PHASE,
            "uptime_s": uptime,
            "queue_size": queue.qsize(),
            "ws_active": ws_started,
            "bg_tasks": len(bg_tasks),
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        })
    except Exception as e:
        logger.warning(f"[API] /api/system/continuity failed: {e}")
        return jsonify({"error": str(e)}), 500

# -------------------------
# SocketIO events
# -------------------------
@sio.event
async def connect(sid, environ):
    logger.info(f"[SocketIO] Client connected: {sid}")


@sio.event
async def disconnect(sid):
    logger.info(f"[SocketIO] Client disconnected: {sid}")


# -------------------------
# Main entrypoint (used for local dev)
# -------------------------
def main():
    parser = argparse.ArgumentParser(description="Crypto Futures Dashboard (Quart backend)")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")))
    args = parser.parse_args()

    port = args.port

    # avoid messing with signals when running under hypercorn/uvicorn import-based ASGI
    if __name__ == "__main__":
        loop = asyncio.get_event_loop()
        try:
            signal.signal(signal.SIGINT, lambda s, f: loop.stop())
            signal.signal(signal.SIGTERM, lambda s, f: loop.stop())
        except Exception:
            # some environments might disallow signal setting
            pass

    logger.info(f"Starting Quart+SocketIO ASGI server on 0.0.0.0:{port}")
    import hypercorn.asyncio
    from hypercorn.config import Config

    config = Config()
    config.bind = [f"0.0.0.0:{port}"]
    asyncio.run(hypercorn.asyncio.serve(asgi_app, config))


if __name__ == "__main__":
    main()

# export asgi_app for uvicorn/hypercorn
# NOTE: some imports reference `app` expecting a Quart app; keep `app` as alias to asgi_app for compatibility.
app = asgi_app
