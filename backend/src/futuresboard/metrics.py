# backend/src/futuresboard/metrics.py
from __future__ import annotations

import os
import asyncio
import random
from datetime import datetime
from typing import List
from dotenv import load_dotenv

load_dotenv()

DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
DB_PATH = os.getenv("DB_PATH", "backend/src/futuresboard/futures.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

import ccxt.async_support as ccxt_async  # type: ignore
import numpy as np
import pandas as pd

from flask import Blueprint, request, jsonify, current_app
from .db import save_metrics_v3 as save_metrics, get_latest_metrics, get_metrics_by_symbol


metrics_bp = Blueprint("metrics", __name__, url_prefix="/api")

# concurrency guard
semaphore = asyncio.Semaphore(8)

def calc_rsi(closes, period=14):
    arr = np.asarray(closes, dtype=float)
    if arr.size < period + 1:
        return 50.0
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return float(np.round(rsi, 2))

@metrics_bp.route("/metrics")
def api_metrics():
    tf = request.args.get("tf", "5m")
    exch = request.args.get("exch", "binance")
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
    tf = request.args.get("tf")
    limit = int(request.args.get("limit", 100))
    try:
        hist = get_metrics_by_symbol(symbol, limit=limit, tf=tf)
        out = []
        for m in hist:
            out.append({
                "time": int(m.timestamp.timestamp() * 1000) if m.timestamp else 0,
                "price": m.price,
                "oi_abs_usd": m.oi_abs_usd,
                "vol_usd": m.vol_usd,
                f"global_ls_{tf or m.timeframe}": getattr(m, f"global_ls_{tf or m.timeframe}", None),
            })
        return jsonify(out)
    except Exception as e:
        current_app.logger.warning(f"history error: {e}")
        return jsonify([])

def add_metrics_route(app):
    # placeholder - kept for compatibility with app.init_app
    return

async def get_all_metrics(tf="5m", exch="binance", limit=20, offset=0) -> List[dict]:
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
        tickers = await exchange.fetch_tickers()
        # simple select of top quoteVolume futures pairs including USDT
        markets = list(tickers.items())
        markets = [k for k, v in tickers.items() if "/USDT:USDT" in k and v.get("quoteVolume", 0) > 1e7]
        # sort by quoteVolume
        markets_sorted = sorted(markets, key=lambda s: tickers[s].get("quoteVolume", 0), reverse=True)
        if limit is not None:
            markets_sorted = markets_sorted[offset: offset + limit]
        raw_symbols = [s.replace("/USDT:USDT", "USDT") for s in markets_sorted]

        results = []
        chunk_size = 10
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
            # small jitter per chunk
            await asyncio.sleep(random.uniform(0.1, 0.5))
        # compute weighted OI if vol_usd present
        if len(results) > 0:
            df = pd.DataFrame(results)
            if "vol_usd" in df.columns and df["vol_usd"].sum() > 0:
                weights = df["vol_usd"] / df["vol_usd"].sum()
                weighted_oi = (df["oi_abs_usd"] * weights).sum()
                for i, _ in enumerate(results):
                    results[i]["weighted_oi_usd"] = float(weighted_oi)
        return results
    finally:
        await exchange.close()

async def fetch_metrics(exchange, ccxt_symbol, raw_symbol, tf="5m"):
    """
    Fetch open interest, ticker, L/S, klines etc. Returns a dict aligned with save_metrics.
    """
    try:
        await asyncio.sleep(0.05)  # small throttle
        oi_data = await exchange.fetch_open_interest(ccxt_symbol)
        ticker = await exchange.fetch_ticker(ccxt_symbol)
        last = ticker.get("last") or ticker.get("close") or 0.0
        oi_amount = oi_data.get("openInterestAmount", 0)
        oi_usd = float(oi_amount) * float(last)
        vol_usd = float(ticker.get("quoteVolume", 0)) or 0.0

        # best-effort L/S via public endpoints using requests (sync) - keep time small
        global_ls = None
        try:
            from .scraper import send_public_request
            _, ls_resp = send_public_request("/futures/data/globalLongShortAccountRatio", {"symbol": raw_symbol, "period": tf})
            if ls_resp and isinstance(ls_resp, list) and len(ls_resp) > 0:
                global_ls = ls_resp[0].get("longShortRatio")
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
