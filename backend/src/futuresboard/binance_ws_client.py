# backend/src/futuresboard/binance_ws_client.py
"""
Modern Binance Futures WS client using CCXT ≥1.95 (Pro features merged).
Replaces custom aiohttp code. Streams mark price and open interest for selected pairs.
"""

import asyncio
import ccxt.async_support as ccxt
from datetime import datetime
import logging
from .db import get_db_conn

logger = logging.getLogger(__name__)


async def stream_worker(pairs):
    """Continuously stream futures data via CCXT's built-in websocket API."""
    exchange = ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    try:
        while True:
            for sym in pairs:
                symbol = f"{sym.replace('USDT', '')}/USDT:USDT"
                try:
                    ticker = await exchange.watch_ticker(symbol)
                    mark_price = float(ticker.get("last") or ticker.get("close") or 0.0)

                    # optional: watch open interest (requires futures)
                    try:
                        oi = await exchange.watch_open_interest(symbol)
                        open_interest = float(oi.get("openInterestAmount") or 0.0)
                    except Exception:
                        open_interest = None

                    # save to DB (lightweight inline write)
                    conn = get_db_conn()
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO metrics (symbol, timeframe, oi_abs_usd, price, timestamp)
                        VALUES (?, '1h', ?, ?, ?)
                        """,
                        (sym.replace("USDT", ""), open_interest or 0.0, mark_price, datetime.utcnow()),
                    )
                    conn.commit()
                    conn.close()
                    logger.info(f"[WS] {sym}: price={mark_price:.2f}, OI={open_interest}")
                except Exception as e:
                    logger.warning(f"[WS] Error for {sym}: {e}")
                    await asyncio.sleep(2)
            await asyncio.sleep(1)
    finally:
        await exchange.close()


def start_stream_worker(pairs):
    """Synchronous entry point for app.py background thread."""
    asyncio.run(stream_worker(pairs))
