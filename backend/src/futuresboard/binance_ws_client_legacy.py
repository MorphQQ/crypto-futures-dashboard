# backend/src/futuresboard/binance_ws_client.py
"""
Async CCXT-based stream worker using watch_* (ccxt v4.x with ccxt.pro merged).
This will subscribe to tickers and optionally other watch_* endpoints.
"""

import asyncio
import logging
import os
import math
import json
from typing import List
from datetime import datetime
import random
import ccxt.async_support as ccxt  # ccxt v4+ (with pro merged)

logger = logging.getLogger("futuresboard.binance_ws_client")
logger.setLevel(logging.INFO)

# Default subscribe set (lowercase symbol names for watchTicker)
DEFAULT_PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]

# Helper to normalize symbol to repo format (BTCUSDT)
def norm_sym(ccxt_sym: str) -> str:
    # handle both "BTC/USDT:USDT" and "BTC/USDT"
    return ccxt_sym.replace("/", "").replace(":USDT", "")

async def start_ccxt_stream(exchange_id: str = "binance", symbols: List[str] = None, on_message=None):
    """
    Start an async CCXT exchange instance and watch tickers for `symbols`.
    on_message is a coroutine callback receiving (symbol, payload_dict).
    """
    if symbols is None or not symbols:
        symbols = DEFAULT_PAIRS

    # instantiate exchange
    ex_cls = getattr(ccxt, exchange_id, None)
    if ex_cls is None:
        logger.error(f"Exchange {exchange_id} not found in ccxt.async_support")
        return

    exchange = ex_cls({
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
        **({"apiKey": os.getenv("API_KEY"), "secret": os.getenv("API_SECRET")} if os.getenv("API_KEY") else {})
    })

    # ensure markets loaded
    try:
        await exchange.load_markets()
    except Exception as e:
        logger.warning(f"load_markets failed for {exchange_id}: {e}")

    # watch loop with backoff
    backoff = 1
    while True:
        try:
            # watchTickers supports a list; fallback to watchTicker per symbol if not
            # Attempt to watch multiple tickers as one (depends on exchange)
            for sym in symbols:
                # Many exchanges accept market symbols like "BTC/USDT"
                try:
                    ticker = await exchange.watch_ticker(sym)
                except Exception as e_sym:
                    # fallback: try normalized symbol
                    logger.debug(f"watch_ticker {sym} failed: {e_sym}")
                    try:
                        # maybe need to try symbol without :USDT suffix
                        candidate = sym.replace(":USDT", "")
                        ticker = await exchange.watch_ticker(candidate)
                    except Exception as e2:
                        logger.debug(f"watch_ticker fallback failed for {sym}: {e2}")
                        continue

                # Build a simple payload and send to on_message
                if on_message:
                    payload = {
                        "symbol": norm_sym(ticker.get("symbol", sym)),
                        "timestamp": datetime.utcnow().isoformat(),
                        "last": ticker.get("last"),
                        "bid": ticker.get("bid"),
                        "ask": ticker.get("ask"),
                        "info": ticker.get("info", {}),
                    }
                    try:
                        await on_message(payload)
                    except Exception as oom:
                        logger.exception(f"on_message failed: {oom}")

                # small sleep to yield control (per-symbol cadence)
                await asyncio.sleep(0.01)

            backoff = 1  # reset success backoff
        except Exception as e:
            logger.warning(f"CCXT watch loop error: {e}; backing off {backoff}s")
            await asyncio.sleep(min(backoff, 60) + (0.5 * (random.random())))
            backoff = min(backoff * 2 if backoff < 60 else 60, 60)
        # short global yield
        await asyncio.sleep(0.01)

    # cleanup (never reached in normal loop)
    try:
        await exchange.close()
    except Exception:
        pass


# Example on_message - integrate with your DB push or socketio
async def default_on_message(payload):
    # Example: print and optionally write to DB
    logger.debug(f"[WS] {payload.get('symbol')} last={payload.get('last')}")
    # If you want DB push here, import your db helper and call it (be careful with concurrency)

if __name__ == "__main__":
    import sys
    pairs = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")
    # transform to CCXT format: BTC/USDT
    ccxt_pairs = [p.replace("USDT", "/USDT") for p in pairs]
    try:
        asyncio.run(start_ccxt_stream("binance", ccxt_pairs, default_on_message))
    except KeyboardInterrupt:
        pass
