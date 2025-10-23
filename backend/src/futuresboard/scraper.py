# backend/src/futuresboard/scraper.py
"""
Async scraping loop used for scheduled TF snapshots.
Now async-friendly and emits via a small queue for non-blocking socket emission.
"""
from __future__ import annotations
import os
import time
import asyncio
import threading
import random
from queue import Queue, Empty
from datetime import datetime
import logging

logger = logging.getLogger("futuresboard.scraper")
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

from .metrics import get_all_metrics
from .db import save_metrics_v3_async
from .quant_engine import compute_quant_metrics, update_quant_summary

_emit_queue: Queue = Queue(maxsize=1000)

def emit_worker(socketio):
    logger.info("[scraper.emit_worker] started")
    while True:
        try:
            try:
                payload = _emit_queue.get_nowait()
            except Empty:
                time.sleep(0.1)
                continue
            if payload and socketio:
                try:
                    # Non-blocking emit - if socketio is not async, this is fine
                    socketio.emit("metrics_update", payload)
                except Exception as e:
                    logger.warning("[scraper.emit_worker] emit error: %s", e)
            _emit_queue.task_done()
        except Exception as e:
            logger.exception("[scraper.emit_worker] fatal: %s", e)
            time.sleep(1)

async def scrape_tf_loop(tf: str, interval: int = 300, socketio=None):
    """
    Single TF loop: fetch metrics (via get_all_metrics), save to DB, emit to socket.
    """
    logger.info("[scraper] starting scrape loop for %s (interval=%s)", tf, interval)
    while True:
        try:
            metrics = await get_all_metrics(tf=tf)
            if metrics:
                # Save in async DB
                saved = await save_metrics_v3_async(metrics, timeframe=tf)
                logger.info("[scraper] saved %d rows for tf=%s", saved, tf)
                # update quant summary async (non-blocking)
                asyncio.create_task(update_quant_summary())
                # Emit payload
                payload = {"data": metrics, "phase": os.getenv("PHASE", "P3"), "timestamp": datetime.utcnow().isoformat()}
                try:
                    _emit_queue.put_nowait(payload)
                except Exception:
                    logger.warning("[scraper] emit queue full - dropping payload")
            else:
                logger.warning("[scraper] no metrics returned for tf=%s", tf)
        except Exception as e:
            logger.exception("[scraper] scrape error: %s", e)
            await asyncio.sleep(min(120, interval * 2))
        await asyncio.sleep(interval)

def start_background_scraper(socketio=None):
    """
    Start emit worker thread and spawn async event loop in background threads for scrape loops.
    """
    # start emit worker in a thread
    t = threading.Thread(target=emit_worker, args=(socketio,), daemon=True)
    t.start()

    # start async scraper loops in a separate thread with event loop
    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        tf_intervals = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600}
        tasks = []
        for tf, interval in tf_intervals.items():
            # stagger a bit to avoid spike at startup
            delay = random.uniform(0, 5)
            async def starter(tf=tf, interval=interval, delay=delay):
                await asyncio.sleep(delay)
                await scrape_tf_loop(tf, interval, socketio=socketio)
            tasks.append(loop.create_task(starter()))
        try:
            loop.run_forever()
        finally:
            for tsk in tasks:
                tsk.cancel()
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
    thr = threading.Thread(target=_runner, daemon=True)
    thr.start()
    logger.info("[scraper] background scraper started")
    return thr
