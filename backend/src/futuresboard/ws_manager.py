"""
Production-ready Binance Futures WebSocket manager (aiohttp).
Exports start_all(symbols, on_message_callback) and stop_all() for lifecycle control.

Behavior:
- Groups streams into connections (max streams per conn).
- Reconnects with exponential backoff.
- Uses shared, cancellable tasks and single ClientSession per manager.
- on_message_callback(payload: dict) should be async and lightweight (e.g., push to queue).
"""
from __future__ import annotations
import asyncio
import json
import logging
import random
import aiohttp
import pathlib
import contextlib
from typing import Callable, Iterable, List, Optional
from .config import get_settings
cfg = get_settings()

MAX_STREAMS_PER_CONN = cfg.MAX_STREAMS_PER_CONN
logger = logging.getLogger("futuresboard.ws_manager")
logger.setLevel(logging.INFO)

BINANCE_FUTURES_COMBINED = "wss://fstream.binance.com/stream?streams="
DEFAULT_STREAMS = ["ticker", "markPrice", "openInterest", "depth@100ms", "aggTrade"]
from .config import get_settings
cfg = get_settings()

MAX_STREAMS_PER_CONN = cfg.MAX_STREAMS_PER_CONN
WS_READ_TIMEOUT = 60
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0

# -- module-level manager state (single instance behavior) --
_manager_lock = asyncio.Lock()
_manager_session: Optional[aiohttp.ClientSession] = None
_manager_tasks: List[asyncio.Task] = []
_manager_stop: Optional[asyncio.Event] = None
_manager_symbols: List[str] = []

# ---------- helpers ----------
def _norm_for_path(sym: str) -> str:
    s = sym.replace("/", "").replace(":USDT", "").replace(":USDT:USDT", "")
    return s.lower()

def _streams_for_symbol(sym: str, streams: Optional[Iterable[str]] = None) -> List[str]:
    if streams is None:
        streams = DEFAULT_STREAMS
    base = _norm_for_path(sym)
    tokens = [f"{base}@{s}" for s in streams]
    return tokens

def _parse_raw_message(raw: str) -> Optional[dict]:
    try:
        j = json.loads(raw)
    except Exception:
        return None
    out = {}
    if isinstance(j, dict) and "stream" in j and "data" in j:
        data = j["data"]
        out["raw"] = data
        symbol = data.get("s") or data.get("symbol")
        if not symbol:
            stream_name = j.get("stream", "")
            if "@" in stream_name:
                symbol = stream_name.split("@", 1)[0].upper()
        if symbol:
            out["symbol"] = symbol.replace("/", "").replace(":USDT", "").upper()
        ts = data.get("E") or data.get("T") or data.get("time")
        out["timestamp"] = ts
        last = data.get("c") or data.get("last") or data.get("p") or data.get("markPrice")
        if last is not None:
            out["last"] = last
        if "openInterest" in data:
            out["openInterest"] = data.get("openInterest")
        if "bids" in data or "asks" in data:
            out["bids"] = data.get("bids")
            out["asks"] = data.get("asks")
        if "b" in data or "a" in data:
            out["bid"] = data.get("b")
            out["ask"] = data.get("a")
        return out
    if isinstance(j, dict):
        out["raw"] = j
        if "s" in j:
            out["symbol"] = j.get("s").replace("/", "").replace(":USDT", "").upper()
        if "c" in j:
            out["last"] = j.get("c")
        if "openInterest" in j:
            out["openInterest"] = j.get("openInterest")
        return out
    return None

# ---------- single connection worker ----------
async def _run_single_connection(session: aiohttp.ClientSession,
                                 stream_tokens: list[str],
                                 cb: Callable[[dict], "asyncio.Future"],
                                 stop_event: asyncio.Event):
    from aiohttp import WSMsgType
    if not stream_tokens:
        return

    stream_path = "/".join(stream_tokens)
    url = BINANCE_FUTURES_COMBINED + stream_path
    backoff = INITIAL_BACKOFF

    while not stop_event.is_set():
        try:
            logger.info(f"[ws] connecting -> {url} (streams={len(stream_tokens)})")
            async with session.ws_connect(url, timeout=WS_READ_TIMEOUT, heartbeat=20) as ws:
                logger.info(f"[ws] connected ({len(stream_tokens)} streams)")
                backoff = INITIAL_BACKOFF
                async for msg in ws:
                    if stop_event.is_set():
                        break
                    if msg.type == WSMsgType.TEXT:
                        parsed = _parse_raw_message(msg.data)
                        if parsed:
                            try:
                                # schedule callback with cancellation guard
                                task = asyncio.create_task(cb(parsed))
                                task.set_name(f"ws_cb_{parsed.get('symbol','')}")
                                task.add_done_callback(
                                    lambda t: t.exception()  # suppress unhandled warnings
                                )
                            except asyncio.CancelledError:
                                logger.info("[ws] callback task cancelled")
                                raise
                            except Exception as e:
                                logger.exception("[ws] callback scheduling error: %s", e)
                    elif msg.type == WSMsgType.CLOSED:
                        logger.warning("[ws] websocket closed by server")
                        break
                    elif msg.type == WSMsgType.ERROR:
                        logger.error("[ws] websocket error: %s", msg)
                        break
        except asyncio.CancelledError:
            logger.info("[ws] connection task cancelled")
            break
        except Exception as e:
            logger.warning(f"[ws] connection error: {e}; reconnecting in {backoff:.1f}s")
            await asyncio.sleep(backoff + (0.5 * random.random()))
            backoff = min(backoff * 2, MAX_BACKOFF)
            continue
    logger.info("[ws] _run_single_connection exiting safely")

# ---------- high-level lifecycle ----------
async def start_all(symbols: Optional[List[str]] = None,
                    on_message_callback: Optional[Callable[[dict], "asyncio.Future"]] = None,
                    streams: Optional[Iterable[str]] = None,
                    max_per_conn: int = MAX_STREAMS_PER_CONN):
    """
    Start manager for provided symbols. If manager already running, this is a no-op.
    """
    global _manager_session, _manager_tasks, _manager_stop, _manager_symbols

    async with _manager_lock:
        if _manager_tasks:
            logger.warning("[ws_manager] start_all called while already running")
            return

        if on_message_callback is None:
            async def _noop(_):
                return
            on_message_callback = _noop

        if not symbols:
            # try config fallback
            try:
                from .config import Config
                cfg = Config.from_config_dir(pathlib.Path.cwd())
                symbols = cfg.SYMBOLS
            except Exception:
                symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

        symbols = [s.strip() for s in symbols if s and s.strip()]
        _manager_symbols = symbols

        # build tokens and groups
        all_tokens: List[str] = []
        for s in symbols:
            all_tokens.extend(_streams_for_symbol(s, streams))
        groups: List[List[str]] = []
        cur: List[str] = []
        for tok in all_tokens:
            if len(cur) >= max_per_conn:
                groups.append(cur)
                cur = []
            cur.append(tok)
        if cur:
            groups.append(cur)

        logger.info(f"[ws_manager] creating {len(groups)} connections for {len(symbols)} symbols: {symbols}")

        _manager_stop = asyncio.Event()
        _manager_session = aiohttp.ClientSession()
        _manager_tasks = [
            asyncio.create_task(_run_single_connection(_manager_session, grp, on_message_callback, _manager_stop))
            for grp in groups
        ]
        logger.info("[ws_manager] started (tasks created)")

async def stop_all(timeout: float = 1.0):
    """
    Stop all manager tasks and close HTTP session.
    """
    global _manager_session, _manager_tasks, _manager_stop
    async with _manager_lock:
        if not _manager_tasks:
            logger.info("[ws_manager] stop_all called — no active tasks")
            return
        logger.info(f"[ws_manager] stopping ({len(_manager_tasks)} tasks)")
        if _manager_stop:
            _manager_stop.set()
        # cancel tasks
        for t in _manager_tasks:
            t.cancel()
        # wait short time
        await asyncio.sleep(timeout)
        # ensure gather to suppress exceptions
        await asyncio.gather(*_manager_tasks, return_exceptions=True)
        _manager_tasks = []
        # close session
        if _manager_session:
            await _manager_session.close()
            _manager_session = None
        _manager_stop = None
        logger.info("[ws_manager] stopped (graceful)")

# convenience run wrapper for top-level long-running invocation
async def run(symbols: Iterable[str], callback: Callable[[dict], "asyncio.Future"]):
    await start_all(list(symbols), callback)
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("[ws_manager.run] cancelled, stopping manager")
        await stop_all()
        raise
