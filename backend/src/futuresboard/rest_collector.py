# backend/src/futuresboard/rest_collector.py
from __future__ import annotations
import asyncio
import os
import aiohttp
import logging
from datetime import datetime, timezone
from typing import Dict, Any
from dotenv import load_dotenv
from . import db
import json

load_dotenv()

logger = logging.getLogger("rest_collector")

API_BASE = os.getenv("API_BASE_URL", "https://fapi.binance.com")
SYMBOLS_ENV = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
SYMBOLS = [s.strip().upper() for s in SYMBOLS_ENV.split(",") if s.strip()]
POLL_INTERVAL = int(os.getenv("REST_POLL_INTERVAL", "10"))
CONCURRENCY = int(os.getenv("REST_CONCURRENCY", "10"))

_sem = asyncio.Semaphore(CONCURRENCY)


async def fetch_symbol(session: aiohttp.ClientSession, symbol: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    out: Dict[str, Any] = {"ts": now, "symbol": symbol}
    try:
        async with _sem:
            async with session.get(f"{API_BASE}/fapi/v1/ticker/24hr",
                                   params={"symbol": symbol}, timeout=10) as r:
                j = await r.json()
                out.update({
                    "open": j.get("openPrice"),
                    "high": j.get("highPrice"),
                    "low": j.get("lowPrice"),
                    "close": j.get("lastPrice"),
                    "volume": j.get("volume"),
                    "trades": int(j.get("count") or 0)
                })

            async with session.get(f"{API_BASE}/fapi/v1/openInterest",
                                   params={"symbol": symbol}, timeout=10) as r:
                j = await r.json()
                out["oi"] = j.get("openInterest")

            async with session.get(f"{API_BASE}/fapi/v1/premiumIndex",
                                   params={"symbol": symbol}, timeout=10) as r:
                j = await r.json()
                out["mark_price"] = j.get("markPrice")
                out["funding_rate"] = j.get("lastFundingRate")

            # additional optional endpoints (best-effort)
            try:
                async with session.get(f"{API_BASE}/futures/data/globalLongShortAccountRatio",
                                       params={"symbol": symbol, "period": "5m", "limit": 1},
                                       timeout=10) as r:
                    j = await r.json()
                    if isinstance(j, list) and j:
                        out["global_long_short_ratio"] = float(j[0].get("longShortRatio") or 0)
            except Exception:
                pass

            try:
                async with session.get(f"{API_BASE}/futures/data/openInterestHist",
                                       params={"symbol": symbol, "period": "5m", "limit": 1},
                                       timeout=10) as r:
                    j = await r.json()
                    if isinstance(j, list) and j:
                        out["open_interest_hist_usd"] = float(j[0].get("sumOpenInterestValue") or 0)
            except Exception:
                pass

            try:
                async with session.get(f"{API_BASE}/futures/data/topLongShortAccountRatio",
                                       params={"symbol": symbol, "period": "5m", "limit": 1},
                                       timeout=10) as r:
                    j = await r.json()
                    if isinstance(j, list) and j:
                        out["top_trader_account_ratio"] = float(j[0].get("longShortRatio") or 0)
            except Exception:
                pass

            try:
                async with session.get(f"{API_BASE}/futures/data/topLongShortPositionRatio",
                                       params={"symbol": symbol, "period": "5m", "limit": 1},
                                       timeout=10) as r:
                    j = await r.json()
                    if isinstance(j, list) and j:
                        out["top_trader_long_short_ratio"] = float(j[0].get("longShortRatio") or 0)
            except Exception:
                pass

    except Exception as e:
        out["error"] = str(e)

    return out


def safe_num(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


async def poll_loop():
    logger.info("[rest_collector] poll_loop started ✅ (initializing DB + session)")
    try:
        await db.init_db_async()
    except Exception as e:
        logger.exception(f"[rest_collector] DB init failed: {e}")
        return

    async with aiohttp.ClientSession() as session:
        logger.info(f"[rest_collector] entering main loop (symbols={SYMBOLS}, interval={POLL_INTERVAL}s)")
        while True:
            try:
                logger.info(f"[rest_collector] polling {len(SYMBOLS)} symbols via REST API")
                tasks = [asyncio.create_task(fetch_symbol(session, s)) for s in SYMBOLS]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # logging sample
                logger.debug("[rest_collector] raw fetch results sample:")
                for r in results[:3]:
                    if isinstance(r, Exception):
                        logger.warning(f"[rest_collector] fetch exception: {r}")
                    else:
                        logger.debug(json.dumps(r, indent=2, default=str))

                rest_rows = []
                metrics_rows = []

                for res in results:
                    if isinstance(res, Exception):
                        logger.warning(f"[rest_collector] error: {res}")
                        continue

                    # ensure ts is a datetime
                    ts_val = res.get("ts")
                    if isinstance(ts_val, str):
                        try:
                            from dateutil import parser as _p
                            ts_val = _p.isoparse(ts_val)
                        except Exception:
                            ts_val = datetime.now(timezone.utc)

                    rest_row = {
                        "ts": ts_val,
                        "symbol": res.get("symbol"),
                        "open": safe_num(res.get("open")),
                        "high": safe_num(res.get("high")),
                        "low": safe_num(res.get("low")),
                        "close": safe_num(res.get("close")),
                        "volume": safe_num(res.get("volume")),
                        "trades": int(res.get("trades") or 0),
                        "oi": safe_num(res.get("oi")),
                        "funding_rate": safe_num(res.get("funding_rate")),
                        "mark_price": safe_num(res.get("mark_price")),
                        "global_long_short_ratio": safe_num(res.get("global_long_short_ratio")),
                        "top_trader_long_short_ratio": safe_num(res.get("top_trader_long_short_ratio")),
                        "top_trader_account_ratio": safe_num(res.get("top_trader_account_ratio")),
                        "open_interest_hist_usd": safe_num(res.get("open_interest_hist_usd")),
                        "metadata": {"raw": res},
                    }
                    rest_rows.append(rest_row)

                    # prepare merged view for metrics table (save_metrics expects many fields)
                    metrics_row = {
                        "symbol": res.get("symbol"),
                        "timeframe": "1m",
                        "price": safe_num(res.get("mark_price")) or safe_num(res.get("close")),
                        "funding": safe_num(res.get("funding_rate")),
                        "oi_usd": safe_num(res.get("open_interest_hist_usd")),
                        "oi_abs_usd": safe_num(res.get("oi")),
                        "global_ls_5m": safe_num(res.get("global_long_short_ratio")),
                        "top_ls_accounts": safe_num(res.get("top_trader_account_ratio")),
                        "top_ls_positions": safe_num(res.get("top_trader_long_short_ratio")),
                        "volume_24h": safe_num(res.get("volume")),
                        "vol_usd": safe_num(res.get("volume")),
                        "market_cap": None,
                        "raw_json": {"rest": res},
                    }
                    metrics_rows.append(metrics_row)

                # insert into market_rest_metrics
                if rest_rows:
                    logger.info(f"[rest_collector] inserting {len(rest_rows)} rows into market_rest_metrics")
                    await db.insert_batch("market_rest_metrics", rest_rows)

                # write merged metrics to metrics table
                if metrics_rows:
                    logger.info(f"[rest_collector] inserting {len(metrics_rows)} merged rows into metrics")
                    # save_metrics_v3_async will coerce and insert
                    await db.save_metrics_v3_async(metrics_rows, timeframe="1m")
                    logger.info("[rest_collector] done inserting merged metrics")

                logger.info(f"[rest_collector] ✅ cycle complete (inserted {len(metrics_rows)} metrics, {len(rest_rows)} rest rows)")
                await asyncio.sleep(POLL_INTERVAL)

            except asyncio.CancelledError:
                logger.info("[rest_collector] cancelled — stopping loop")
                raise
            except Exception as e:
                logger.exception(f"[rest_collector] loop-level error: {e}")
                await asyncio.sleep(POLL_INTERVAL)


async def run(symbols: list[str], out_queue: asyncio.Queue | None = None, interval: int = 60):
    """
    Unified REST collector entrypoint for app.py.
    """
    logger.info(f"[rest_collector] run() invoked (symbols={symbols}, interval={interval})")
    # adapt configured interval if provided
    global POLL_INTERVAL
    try:
        POLL_INTERVAL = int(interval or POLL_INTERVAL)
    except Exception:
        pass

    # run forever (auto-restart on exception)
    while True:
        try:
            await poll_loop()
        except asyncio.CancelledError:
            logger.info("[rest_collector] cancelled — shutting down gracefully")
            raise
        except Exception as e:
            logger.exception(f"[rest_collector] fatal error in run(): {e}")
            await asyncio.sleep(10)
