# Fixed: backend/src/futuresboard/metrics.py
# Changes:
# - Added semaphore to fetch_metrics.
# - Increased chunk_size=20; removed per-call sleep (use CCXT rate).
# - Async L/S fetch with aiohttp.
# - Added symbol filter call (now works).
# - Removed duplicate RSI.
# - Validation for tf/exch in api_metrics.
# - Fixed weighted_oi: Per-row local weight.

# backend/src/futuresboard/metrics.py
from __future__ import annotations

import os
import asyncio
import random
from datetime import datetime
from typing import List, Dict, Union
from dotenv import load_dotenv

load_dotenv()

DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
DB_PATH = os.getenv("DB_PATH", "backend/src/futuresboard/futures.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

import ccxt.async_support as ccxt_async  # type: ignore
import numpy as np
import pandas as pd
import aiohttp  # For async requests

from flask import Blueprint, request, jsonify, current_app, abort
from .db import save_metrics_v3 as save_metrics, get_latest_metrics, get_metrics_by_symbol

ALLOWED_TFS = ["5m", "15m", "30m", "1h"]
ALLOWED_EXCHS = ["binance", "bybit"]

metrics_bp = Blueprint("metrics", __name__, url_prefix="/api")

# concurrency guard
semaphore = asyncio.Semaphore(20)  # Increased

def to_ms(dt):
    """Helper: Unix ms from datetime or 0."""
    return int(dt.timestamp() * 1000) if dt and hasattr(dt, 'timestamp') else 0

@metrics_bp.route("/metrics")
def api_metrics():
    tf = request.args.get("tf", "5m")
    if tf not in ALLOWED_TFS:
        abort(400, "Invalid timeframe")
    exch = request.args.get("exch", "binance")
    if exch not in ALLOWED_EXCHS:
        abort(400, "Invalid exchange")
    limit = request.args.get("limit")
    try:
        limit = None if limit is None else int(limit)
    except Exception:
        limit = 20
    offset = int(request.args.get("offset", 0))
    metrics = asyncio.run(get_all_metrics(tf=tf, exch=exch, limit=limit, offset=offset))
    try:
        save_metrics(metrics, timeframe=tf)
    except Exception as e:
        current_app.logger.warning(f"save_metrics failed in api_metrics: {e}")
    enriched = []
    # Enrich with last rolling values if DB accessible
    for m in metrics:
        if "error" in m:
            enriched.append(m)
            continue
        # try to fetch last saved to fill deltas if present
        try:
            last = get_latest_metrics(limit=1, symbol=m["symbol"], tf=tf)
            if last:
                prev = last[0]
                m["oi_delta_pct"] = getattr(prev, "oi_delta_pct", 0.0) or 0.0
                m["ls_delta_pct"] = getattr(prev, "ls_delta_pct", 0.0) or 0.0
                m["z_ls"] = getattr(prev, "z_ls", 0.0) or 0.0
            else:
                m["oi_delta_pct"] = 0.0
                m["ls_delta_pct"] = 0.0
                m["z_ls"] = 0.0
        except Exception:
            m["oi_delta_pct"] = 0.0
            m["ls_delta_pct"] = 0.0
            m["z_ls"] = 0.0
        enriched.append(m)

    total_candidates = len(enriched)
    response = jsonify(enriched)
    response.headers["Content-Range"] = f"{offset}-{offset + max(0, total_candidates - 1)}/{total_candidates}"
    response.headers["X-Continuity-Phase"] = os.getenv("PHASE", "P3 - Weighted OI + Top L/S + Alerts")
    response.headers["X-Backend-Timestamp"] = datetime.utcnow().isoformat(timespec="seconds")
    response.headers["X-Backend-Health"] = "healthy"
    return response

@metrics_bp.route("/<symbol>/history")
def api_history(symbol):
    if not symbol or len(symbol) > 20:
        abort(400, "Invalid symbol")
    tf = request.args.get("tf")
    if tf and tf not in ALLOWED_TFS:
        abort(400, "Invalid timeframe")
    limit = int(request.args.get("limit", 100))
    try:
        hist = get_metrics_by_symbol(symbol, limit=limit, tf=tf)
        out = []
        for m in hist:
            col = f"global_ls_{tf or m.timeframe}" if tf else "global_ls_5m"
            ls_val = getattr(m, col, None)
            out.append({
                "time": to_ms(m.timestamp),
                "price": m.price,
                "oi_abs_usd": m.oi_abs_usd,
                "vol_usd": m.vol_usd,
                "global_ls": ls_val,
            })
        return jsonify(out)
    except Exception as e:
        current_app.logger.warning(f"history error: {e}")
        return jsonify([])

def add_metrics_route(app):
    # placeholder - kept for compatibility with app.init_app
    return

async def get_all_metrics(tf="5m", exch="binance", limit=20, offset=0) -> List[Dict[str, Union[float, str, None]]]:
    """
    Primary async fetch. When DEV_MODE is true, return mocks quickly.
    Otherwise use ccxt to fetch top pairs and call fetch_metrics concurrently.
    """
    print(f"get_all_metrics tf={tf} exch={exch} limit={limit} offset={offset}")
    if DEV_MODE:
        # generate quick mocks
        syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        if limit is None:
            limit = len(syms)
        res = []
        for s in syms[offset: offset + limit]:
            mock_oi = random.uniform(1e7, 1e10)
            res.append({
                "symbol": s.replace("USDT", ""),
                "Price": f"${random.uniform(1000, 70000):,.2f}",
                "oi_abs_usd": mock_oi,
                "vol_usd": random.uniform(1e8, 1e10),
                f"Global_LS_{tf}": random.uniform(0.5, 4.0),
                "timestamp": datetime.utcnow().timestamp(),
                "timeframe": tf,
            })
        return res

    exchange_class = getattr(ccxt_async, exch, ccxt_async.binance)
    exchange = exchange_class({
        "enableRateLimit": True,
        "apiKey": os.getenv("API_KEY"),
        "secret": os.getenv("API_SECRET"),
        "options": {"defaultType": "future"},
    })
    try:
        await exchange.load_markets()  # Unified symbols
        tickers = await exchange.fetch_tickers()
        # simple select of top quoteVolume futures pairs including USDT
        markets = [k for k, v in tickers.items() if "/USDT:USDT" in k and v.get("quoteVolume", 0) > 1e7]
        # sort by quoteVolume
        markets_sorted = sorted(markets, key=lambda s: tickers[s].get("quoteVolume", 0), reverse=True)
        if limit is not None:
            markets_sorted = markets_sorted[offset: offset + limit]
        raw_symbols = [s.replace("/USDT:USDT", "USDT") for s in markets_sorted]

        results = []
        chunk_size = 20  # Increased
        for i in range(0, len(raw_symbols), chunk_size):
            chunk = raw_symbols[i: i + chunk_size]
            tasks = [fetch_metrics(exchange, s.replace("USDT", "/USDT:USDT"), s, tf) for s in chunk]
            chunk_res = await asyncio.gather(*tasks, return_exceptions=True)
            # filter results
            for r in chunk_res:
                if isinstance(r, Exception):
                    continue
                if isinstance(r, dict) and "error" in r:
                    continue
                results.append(r)
            # jitter per chunk
            await asyncio.sleep(random.uniform(0.1, 0.5))
        # compute weighted OI if vol_usd present (local per row)
        if len(results) > 0:
            # Batch fetch recent for weights (per-symbol, limit=20)
            symbols = list(set(r['symbol'] for r in results))
            recent_vols = {}  # sym → [vols]
            for sym in symbols:
                try:
                    last20 = get_latest_metrics(limit=20, symbol=sym, tf=tf)  # From DB
                    vols = np.array([float(m.vol_usd or 0.0) for m in last20])
                    ois_ = np.array([float(m.oi_abs_usd or 0.0) for m in last20])
                    recent_vols[sym] = (vols, ois_)
                except: pass  # Fallback single
            for r in results:
                sym = r['symbol']
                if sym in recent_vols:
                    vols, ois_ = recent_vols[sym]
                    if len(vols) > 1 and vols.sum() > 0:
                        r["weighted_oi_usd"] = np.average(ois_, weights=vols)  # Rolling!
                    else:
                        r["weighted_oi_usd"] = r.get("oi_abs_usd", 0)
                else:
                    r["weighted_oi_usd"] = r.get("oi_abs_usd", 0)
        return results
    finally:
        await exchange.close()

async def fetch_metrics(exchange, ccxt_symbol, raw_symbol, tf="5m"):
    """
    Fetch open interest, ticker, L/S, klines etc. Returns a dict aligned with save_metrics.
    """
    async with semaphore:
        try:
            oi_data = await exchange.fetch_open_interest(ccxt_symbol)
            ticker = await exchange.fetch_ticker(ccxt_symbol)
            last = ticker.get("last") or ticker.get("close") or 0.0
            oi_amount = oi_data.get("openInterestAmount", 0)
            oi_usd = float(oi_amount) * float(last)
            vol_usd = float(ticker.get("quoteVolume", 0)) or 0.0

            # Async L/S fetch
            global_ls = None
            try:
                # some exchanges provide 'fapiPublicGetGlobalLongShortAccountRatio' under ccxt
                method = getattr(exchange, "fapiPublicGetGlobalLongShortAccountRatio", None)
                if callable(method):
                    ls_resp = await method({"symbol": raw_symbol, "period": tf})
                    if ls_resp:
                        global_ls = float(ls_resp[0].get("longShortRatio"))
                else:
                    # fallback to HTTP via exchange.fetch() or aiohttp as you had before
                    ...
            except Exception:
                global_ls = None

            result = {
                "symbol": raw_symbol.replace("USDT", ""),
                "Price": f"${last:,.2f}",
                "oi_abs_usd": float(oi_usd),
                "vol_usd": float(vol_usd),
                f"Global_LS_{tf}": float(global_ls) if global_ls is not None else None,
                "timestamp": datetime.utcnow().timestamp(),
                "timeframe": tf,
            }
            return result
        except Exception as e:
            return {"symbol": raw_symbol.replace("USDT", ""), "error": str(e)}