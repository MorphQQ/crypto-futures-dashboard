# backend/src/futuresboard/quant_engine.py
from __future__ import annotations
import asyncio
import json
import math
import statistics
import random
import warnings
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta, timezone
import numpy as np
from .utils import safe_float, pct_change, safe_corrcoef, zscore, mean_or_none

from . import db
import logging

logger = logging.getLogger("futuresboard.quant_engine")
# Respect external configuration if present; default to INFO
if not logger.handlers:
    logger.setLevel(logging.INFO)

# --- Config / thresholds (tweakable) ---
DEFAULT_HISTORY = 120  # rows per-symbol to fetch for time-series features
ATR_WINDOW = 5         # used as proxy for short-term volatility
VPI_THRESHOLD = 500_000  # example threshold for strong VPI signals
ZSC_ALERT = 1.8        # absolute zsc alert threshold

# family weights used in a simple confidence aggregation
FAMILY_WEIGHTS = {
    "accumulation": 1.0,
    "momentum": 1.0,
    "exhaustion": 0.8,
    "orderflow": 1.0,
    "divergence": 0.9
}


# -------------------------
# Raw JSON parsing helpers
# -------------------------
def parse_raw_json_field(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "raw" in parsed and isinstance(parsed["raw"], str):
                try:
                    parsed["raw"] = json.loads(parsed["raw"])
                except Exception:
                    pass
            return parsed
        except Exception:
            return None
    return None

def extract_book_top_volumes(parsed_raw: dict, top_n: int = 5):
    if not parsed_raw:
        return None, None
    raw_field = parsed_raw.get("raw") if isinstance(parsed_raw, dict) else parsed_raw
    if raw_field is None:
        raw_field = parsed_raw
    if not isinstance(raw_field, dict):
        return None, None

    bids = None
    asks = None
    for k in ("bids", "b", "bid"):
        if k in raw_field:
            bids = raw_field[k]
            break
    for k in ("asks", "a", "ask"):
        if k in raw_field:
            asks = raw_field[k]
            break

    def sum_top(arr):
        if not isinstance(arr, list):
            return None
        s = 0.0
        n = 0
        for i, item in enumerate(arr):
            if i >= top_n:
                break
            try:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    size = safe_float(item[1])
                elif isinstance(item, dict) and ("size" in item or "volume" in item):
                    size = safe_float(item.get("size") or item.get("volume"))
                else:
                    size = None
                if size is not None:
                    s += float(size)
                    n += 1
            except Exception:
                continue
        return s if n > 0 else None

    bid_sum = sum_top(bids)
    ask_sum = sum_top(asks)
    return bid_sum, ask_sum

def extract_taker_counts(parsed_raw: dict, lookback_trades: int = 100):
    if not parsed_raw:
        return None, None
    raw_field = parsed_raw.get("raw") if isinstance(parsed_raw, dict) else parsed_raw
    if not isinstance(raw_field, dict):
        return None, None

    trades = None
    for k in ("trades", "aggTrades", "recent_trades", "agg_trades"):
        if k in raw_field:
            trades = raw_field[k]
            break
    if trades is None:
        return None, None

    buy = 0
    sell = 0
    total = 0
    for t in trades[-lookback_trades:]:
        total += 1
        is_buyer_maker = None
        side = None
        if isinstance(t, dict):
            if "m" in t:
                is_buyer_maker = t.get("m")
            elif "isBuyerMaker" in t:
                is_buyer_maker = t.get("isBuyerMaker")
            if "side" in t:
                side = t.get("side")
            if is_buyer_maker is not None:
                if not is_buyer_maker:
                    buy += 1
                else:
                    sell += 1
            elif side:
                if isinstance(side, str):
                    s = side.lower()
                    if s in ("buy", "b"):
                        buy += 1
                    elif s in ("sell", "s"):
                        sell += 1
            else:
                total -= 1
        else:
            total -= 1
    if total <= 0:
        return None, None
    return buy, sell

# -------------------------
# Safe loop runner (cancellation-aware)
# -------------------------
async def safe_loop_runner(
    name: str,
    iteration_coro: Callable[[], Any],
    interval: float = 5.0,
    flush_coro: Optional[Callable[[], Any]] = None,
    jitter: float = 0.1
):
    """
    Runs `iteration_coro()` repeatedly with cancellable loop, jittered sleep,
    and optional flush on cancel.
    - iteration_coro: async callable performing a single iteration/work unit.
    - flush_coro: optional async callable executed once on cancellation to flush state.
    """
    logger.info(f"[{name}] started (interval={interval}s)")
    try:
        while True:
            try:
                await iteration_coro()
            except asyncio.CancelledError:
                logger.info(f"[{name}] cancelled during iteration — running flush/cleanup")
                raise
            except Exception as e:
                logger.warning(f"[{name}] iteration error: {type(e).__name__}: {e}")
            # jittered sleep to avoid synchronized spikes
            sleep_for = interval + random.uniform(-jitter, jitter)
            if sleep_for < 0:
                sleep_for = interval
            await asyncio.sleep(sleep_for)
    except asyncio.CancelledError:
        # run flush if provided
        if flush_coro:
            try:
                await flush_coro()
                logger.info(f"[{name}] flush/cleanup complete on cancel")
            except Exception as e:
                logger.warning(f"[{name}] flush failed on cancel: {e}")
        logger.info(f"[{name}] exiting")
        raise
    except Exception as e:
        logger.exception(f"[{name}] unexpected fatal error: {e}")
        # try flush before exit
        if flush_coro:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    await flush_coro()
                except Exception:
                    pass
        return

# -------------------------
# Main compute (per-symbol heavy lifting moved to sync function to offload)
# -------------------------
def _process_symbol_sync(sym: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    CPU-heavy processing for one symbol. Executed in thread pool.
    Accepts chronological rows (oldest -> newest).
    Returns dict or None.
    """
    try:
        if not rows:
            return None

        # extract series
        prices = [safe_float(r.get("price")) for r in rows]
        oi_usd = [safe_float(r.get("oi_usd")) for r in rows]
        vol_usd = [safe_float(r.get("vol_usd") or r.get("volume_24h")) for r in rows]
        global_ls = [safe_float(r.get("global_ls_5m") or r.get("Global_LS_5m")) for r in rows]
        top_ls_acc = [safe_float(r.get("top_ls_accounts") or r.get("Top_LS_Accounts")) for r in rows]
        top_ls_pos = [safe_float(r.get("top_ls_positions") or r.get("Top_LS_Positions")) for r in rows]
        funding = [safe_float(r.get("funding") or r.get("funding_rate") or r.get("Funding")) for r in rows]
        raw_jsons = [r.get("raw_json") or r.get("raw") or r.get("rawJson") for r in rows]

        # latest
        latest = rows[-1]
        latest_price = safe_float(latest.get("price"))
        latest_oi = safe_float(latest.get("oi_usd"))
        latest_vol = safe_float(latest.get("vol_usd") or latest.get("volume_24h"))
        latest_global_ls = safe_float(latest.get("global_ls_5m"))
        latest_top_ls_acc = safe_float(latest.get("top_ls_accounts"))
        latest_top_ls_pos = safe_float(latest.get("top_ls_positions"))
        latest_funding = safe_float(latest.get("funding") or latest.get("funding_rate"))

        # helper index pct change
        def idx_pct_change(series, offset):
            if len(series) <= offset or series[-1] is None or series[-1-offset] is None:
                return None
            return pct_change(series[-1], series[-1-offset])

        oi_ch_5 = idx_pct_change(oi_usd, 5)
        oi_ch_10 = idx_pct_change(oi_usd, 10)
        price_ch_5 = idx_pct_change(prices, 5)
        price_ch_10 = idx_pct_change(prices, 10)

        # returns and ATR proxy
        returns = []
        for i in range(1, len(prices)):
            a = prices[i]
            b = prices[i-1]
            if a is None or b is None or b == 0:
                returns.append(None)
            else:
                returns.append((a - b) / b)
        recent_returns = [r for r in returns[-ATR_WINDOW:] if r is not None]
        atr_5s = None
        if recent_returns:
            try:
                atr_5s = statistics.pstdev(recent_returns) * (latest_price or 1.0)
            except Exception:
                atr_5s = None

        # zscores
        z_oi_series = zscore([v for v in oi_usd])
        z_top_acc_series = zscore([v for v in top_ls_acc])
        z_oi_latest = z_oi_series[-1] if z_oi_series else None
        z_top_acc_latest = z_top_acc_series[-1] if z_top_acc_series else None

        # parse latest raw
        parsed_latest_raw = None
        raw_field = raw_jsons[-1] if raw_jsons else None
        parsed_latest_raw = parse_raw_json_field(raw_field)

        bid_top, ask_top = extract_book_top_volumes(parsed_latest_raw, top_n=5)
        obi = None
        if bid_top is not None and ask_top is not None:
            try:
                if (bid_top + ask_top) > 0:
                    obi = bid_top / (bid_top + ask_top)
            except Exception:
                obi = None

        taker_buy_count, taker_sell_count = extract_taker_counts(parsed_latest_raw) or (None, None)
        taker_buy_ratio = None
        taker_sell_ratio = None
        if taker_buy_count is not None and taker_sell_count is not None and (taker_sell_count + taker_buy_count) > 0:
            # ratio expressed as buy/sell and sell/buy
            taker_buy_ratio = taker_buy_count / max(1, taker_sell_count)
            taker_sell_ratio = taker_sell_count / max(1, taker_buy_count)

        # VPI
        vpi = None
        if taker_buy_count is not None and taker_sell_count is not None and latest_vol is not None:
            vpi = (taker_buy_count - taker_sell_count) * (latest_vol or 0) / max(1, (taker_buy_count + taker_sell_count))

        # build obi_series and funding_series for z-scores
        obi_series = []
        for raw in raw_jsons:
            p = parse_raw_json_field(raw)
            b, a = extract_book_top_volumes(p, top_n=5)
            if b is not None and a is not None:
                try:
                    obi_series.append(b / (b + a))
                except Exception:
                    obi_series.append(None)
            else:
                obi_series.append(None)
        funding_series = [safe_float(x) for x in funding] if funding else []

        z_obi = None
        if any(v is not None for v in obi_series):
            z_obi_series = zscore([v for v in obi_series])
            z_obi = z_obi_series[-1] if z_obi_series else None

        z_funding = None
        if funding_series:
            try:
                z_funding_series = zscore([v for v in funding_series])
                z_funding = z_funding_series[-1] if z_funding_series else None
            except Exception:
                z_funding = None

        # zsc composite
        zsc = None
        components = []
        weights = []
        if z_oi_latest is not None:
            components.append((z_oi_latest, 0.4)); weights.append(0.4)
        if z_top_acc_latest is not None:
            components.append((z_top_acc_latest, 0.2)); weights.append(0.2)
        if z_obi is not None:
            components.append((z_obi, 0.2)); weights.append(0.2)
        if z_funding is not None:
            components.append((z_funding, 0.2)); weights.append(0.2)
        if components and sum(weights) > 0:
            zsc = sum(val * w for val, w in components) / sum(weights)

        # atr normalization helper
        def atr_norm(x_pct):
            if x_pct is None or atr_5s is None or latest_price is None or latest_price == 0:
                return None
            try:
                abs_move = abs(x_pct / 100.0 * latest_price)
                return abs_move / atr_5s if atr_5s > 0 else None
            except Exception:
                return None

        # Family heuristics (retain previous logic)
        accum_ok = False
        try:
            cond1 = oi_ch_10 is not None and atr_5s is not None and (oi_ch_10 / 100.0 * (latest_oi or 1)) / (atr_5s or 1) > 1.0 if latest_oi else False
        except Exception:
            cond1 = False
        cond2 = price_ch_10 is not None and atr_norm(price_ch_10) is not None and abs(atr_norm(price_ch_10)) < 0.05
        cond3 = (latest_top_ls_acc is not None and latest_top_ls_acc > 1.5)
        cond4 = (obi is not None and obi > 0.55)
        if cond1 and cond2 and cond3 and cond4:
            accum_ok = True

        distrib_ok = False
        try:
            cond1s = oi_ch_10 is not None and atr_5s is not None and (oi_ch_10 / 100.0 * (latest_oi or 1)) / (atr_5s or 1) > 1.0 if latest_oi else False
        except Exception:
            cond1s = False
        cond2s = price_ch_10 is not None and atr_norm(price_ch_10) is not None and abs(atr_norm(price_ch_10)) < 0.05
        cond3s = (latest_top_ls_acc is not None and latest_top_ls_acc < 0.8)
        cond4s = (obi is not None and obi < 0.45)
        if cond1s and cond2s and cond3s and cond4s:
            distrib_ok = True

        momentum_ok = False
        try:
            norm_oi5 = None
            if oi_ch_5 is not None and atr_5s is not None and latest_oi:
                norm_oi5 = (oi_ch_5 / 100.0 * (latest_oi or 1)) / (atr_5s or 1)
        except Exception:
            norm_oi5 = None
        condm1 = norm_oi5 is not None and norm_oi5 > 0.4
        condm2 = price_ch_5 is not None and atr_norm(price_ch_5) is not None and (price_ch_5 / 100.0 * latest_price) / (atr_5s or 1) > 0.02 if (atr_5s and latest_price) else False
        condm3 = taker_buy_ratio is not None and taker_buy_ratio > 1.2
        condm4 = vpi is not None and abs(vpi) > VPI_THRESHOLD
        if condm1 and condm2 and condm3:
            momentum_ok = True

        exhaustion_ok = False
        try:
            condx1 = price_ch_10 is not None and atr_norm(price_ch_10) is not None and (price_ch_10 / 100.0 * latest_price) / (atr_5s or 1) > 0.06
        except Exception:
            condx1 = False
        condx2 = oi_ch_10 is not None and oi_ch_10 < -0.4
        condx3 = latest_top_ls_acc is not None and latest_top_ls_acc < 0.9
        condx4 = z_oi_latest is not None and z_oi_latest < -0.8
        if condx1 and condx2 and condx3 and condx4:
            exhaustion_ok = True

        orderflow_ok = False
        try:
            bid_prev, ask_prev = None, None
            if len(raw_jsons) >= 6:
                prev_parsed = parse_raw_json_field(raw_jsons[-6])
                bid_prev, ask_prev = extract_book_top_volumes(prev_parsed, top_n=5)
            avg_book_depth = None
            top_depth_vals = [v for v in (bid_top, ask_top, bid_prev, ask_prev) if v is not None]
            if top_depth_vals:
                avg_book_depth = sum(top_depth_vals) / len(top_depth_vals)
            delta_bid = None
            if bid_top is not None and bid_prev is not None:
                delta_bid = (bid_top - bid_prev) / max(1.0, bid_prev)
            condof1 = delta_bid is not None and avg_book_depth and (delta_bid > 0.15)
            condof2 = (ask_top is not None and bid_top is not None and (ask_top / max(1.0, bid_top) < 0.8))
            condof3 = taker_buy_ratio is not None and taker_buy_ratio > 1.3
            if condof1 and condof2 and condof3:
                orderflow_ok = True
        except Exception:
            orderflow_ok = False

        divergence_ok = False
        try:
            if latest_global_ls is not None and latest_top_ls_acc is not None and latest_top_ls_acc != 0:
                retail_vs_pro = latest_global_ls / latest_top_ls_acc
                if retail_vs_pro > 2.5 and (latest_funding is not None and latest_funding > 0.0001) and (obi is not None and obi < 0.45):
                    divergence_ok = True
        except Exception:
            divergence_ok = False

        family_flags = {
            "accumulation": accum_ok,
            "distribution": distrib_ok,
            "momentum": momentum_ok,
            "exhaustion": exhaustion_ok,
            "orderflow": orderflow_ok,
            "divergence": divergence_ok
        }
        score_numer = 0.0
        score_denom = 0.0
        for k, v in family_flags.items():
            w = FAMILY_WEIGHTS.get(k, 1.0)
            score_denom += w
            if v:
                score_numer += w
        confidence = score_numer / score_denom if score_denom > 0 else 0.0

        out = {
            "symbol": sym,
            "timestamp": datetime.utcnow().isoformat(),
            "price": latest_price,
            "oi_usd": latest_oi,
            "vol_usd": latest_vol,
            "global_ls_5m": latest_global_ls,
            "top_ls_accounts": latest_top_ls_acc,
            "top_ls_positions": latest_top_ls_pos,
            "funding": latest_funding,
            "oi_change_5s_pct": oi_ch_5,
            "oi_change_10s_pct": oi_ch_10,
            "price_change_5s_pct": price_ch_5,
            "price_change_10s_pct": price_ch_10,
            "atr_5s": atr_5s,
            "obi": obi,
            "taker_buy_ratio": taker_buy_ratio,
            "taker_sell_ratio": taker_sell_ratio,
            "vpi": vpi,
            "z_oi": z_oi_latest,
            "z_top_ls_acc": z_top_acc_latest,
            "z_obi": z_obi,
            "z_funding": z_funding,
            "zsc": zsc,
            "families": family_flags,
            "confidence": round(float(confidence), 3),
            # include a compact raw snapshot for debugging
            "raw_json": parsed_latest_raw or {}
        }
        return out
    except Exception:
        logger.exception(f"[quant_engine::_process_symbol_sync] failed for {sym}")
        return None

# -------------------------
# High-level compute function (async) that offloads heavy per-symbol compute to threadpool
# -------------------------
async def compute_quant_metrics(limit: int = 200) -> List[Dict[str, Any]]:
    """
    Compute per-symbol quant metrics and heuristic signals.
    Offloads the per-symbol CPU-heavy part to threadpool for event-loop responsiveness.
    """
    try:
        raw_latest = await db.get_latest_metrics_async(limit=limit)
    except Exception as e:
        logger.exception(f"[quant_engine] failed to fetch latest metrics: {e}")
        return []

    # gather unique symbols preserving order
    symbols = []
    seen = set()
    for r in raw_latest:
        sym = r.get("symbol")
        if sym and sym not in seen:
            symbols.append(sym)
            seen.add(sym)

    # --- Replace semaphore + run_in_executor block in compute_quant_metrics ---
    # bounded concurrency for IO + CPU offload
    pool_max = getattr(db, "POOL_MAX_SIZE", 10) or 10
    sem = asyncio.Semaphore(max(1, min(24, pool_max * 2)))

    async def process_symbol(sym: str) -> Optional[Dict[str, Any]]:
        async with sem:
            try:
                rows = await db.get_metrics_by_symbol_async(sym, limit=DEFAULT_HISTORY, tf=None)
                if not rows:
                    return None
                rows = list(reversed(rows))
                # Offload CPU-heavy processing to thread. Note: threads cannot be cancelled.
                result = await asyncio.to_thread(_process_symbol_sync, sym, rows)
                return result
            except Exception as e:
                logger.exception(f"[quant_engine] process_symbol {sym} failed: {e}")
                return None


    tasks = [asyncio.create_task(process_symbol(s)) for s in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # collect valid results
    computed = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[quant_engine] symbol processing raised: {r}")
            continue
        if r:
            computed.append(r)

    # sort by oi_usd desc
    computed.sort(key=lambda x: (x.get("oi_usd") or 0), reverse=True)

    # persist quick HF features asynchronously (non-blocking)
    try:
        await db.save_quant_features_async(computed)
    except Exception as e:
        logger.warning(f"[quant_engine] save_quant_features_async failed: {e}")

    return computed

# -------------------------
# Lightweight summary update
# -------------------------
async def update_quant_summary() -> int:
    try:
        data = await compute_quant_metrics(limit=200)
        logger.debug(f"[quant_engine] update_quant_summary computed {len(data)} symbols")
        # Optionally write to DB here if desired by the system; return count for now
        return len(data)
    except Exception as e:
        logger.exception(f"[quant_engine] update_quant_summary failed: {e}")
        return 0

# -------------------------
# Persist 5s features mapping
# -------------------------
async def persist_5s_features(computed: List[Dict[str, Any]]) -> int:
    try:
        rows = []
        for c in computed:
            ts = c.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except Exception:
                    ts = datetime.utcnow()
            elif ts is None:
                ts = datetime.utcnow()
            row = {
                "symbol": c.get("symbol"),
                "timeframe": c.get("timeframe", "5s"),
                "ts": ts,
                "price": safe_float(c.get("price")),
                "oi_usd": safe_float(c.get("oi_usd")),
                "vol_usd": safe_float(c.get("vol_usd")),
                "oi_change_5s_pct": safe_float(c.get("oi_change_5s_pct")),
                "oi_change_10s_pct": safe_float(c.get("oi_change_10s_pct")),
                "price_change_5s_pct": safe_float(c.get("price_change_5s_pct")),
                "price_change_10s_pct": safe_float(c.get("price_change_10s_pct")),
                "atr_5s": safe_float(c.get("atr_5s")),
                "obi": safe_float(c.get("obi")),
                "taker_buy_ratio": safe_float(c.get("taker_buy_ratio")),
                "taker_sell_ratio": safe_float(c.get("taker_sell_ratio")),
                "vpi": safe_float(c.get("vpi")),
                "z_oi": safe_float(c.get("z_oi")),
                "z_top_ls_acc": safe_float(c.get("z_top_ls_acc")),
                "z_obi": safe_float(c.get("z_obi")),
                "z_funding": safe_float(c.get("z_funding")),
                "zsc": safe_float(c.get("zsc")),
                "confidence": safe_float(c.get("confidence") or 0.0),
                "families": c.get("families") or {},
                "raw_json": c.get("raw_json") or c
            }
            rows.append(row)
        saved = await db.save_quant_features_5s_async(rows)
        return saved
    except Exception as e:
        logger.exception(f"[persist_5s_features] failed: {e}")
        return 0

# -------------------------
# 5s quant loop runner (uses safe_loop_runner)
# -------------------------
async def run_quant_loop(interval: float = 5.0):
    """
    Continuous loop computing metrics at HF cadence and persisting to quant_features_5s.
    Uses safe_loop_runner for cancellation behavior.
    """
    async def iteration():
        computed = []
        try:
            computed = await compute_quant_metrics(limit=200)
        except Exception as e:
            logger.warning(f"[QuantEngine] compute_quant_metrics failed: {e}")
        if computed:
            try:
                await persist_5s_features(computed)
            except Exception as e:
                logger.warning(f"[QuantEngine] persist_5s_features failed: {e}")
            # optional socket emission (best-effort, non-blocking)
            try:
                from .app import sio
                def _safe_json(data):
                    def default(o):
                        if isinstance(o, datetime):
                            return o.isoformat()
                        return str(o)
                    return json.loads(json.dumps(data, default=default))
                await sio.emit("quant_update_5s", {"data": _safe_json(computed), "ts": datetime.utcnow().isoformat()})
            except Exception:
                # non-fatal
                pass

    # flush on cancel: none required (persisted each iteration)
    await safe_loop_runner("QuantEngine", iteration, interval=interval, flush_coro=None)

# -------------------------
# Diagnostics (use async DB context; no explicit release)
# -------------------------
async def compute_quant_diagnostics(symbols: List[str], window_s: int = 60) -> List[dict]:
    results = []
    try:
        async with db.DBConnection() as conn:
            for sym in symbols:
                recs = await conn.fetch(
                    "SELECT ts, price_change_5s_pct, oi_change_5s_pct, taker_buy_ratio, taker_sell_ratio "
                    "FROM quant_features_5s WHERE symbol=$1 ORDER BY ts DESC LIMIT 120", sym
                )
                if len(recs) < 6:
                    continue
                price_deltas = [r["price_change_5s_pct"] or 0 for r in recs]
                oi_deltas    = [r["oi_change_5s_pct"] or 0 for r in recs]
                ls_ratio     = [((r["taker_buy_ratio"] or 0) - (r["taker_sell_ratio"] or 0)) for r in recs]

                v5 = 0.0
                try:
                    norm_price_deltas = [float(x) for x in price_deltas if x is not None]
                    v5 = float(np.std(norm_price_deltas)) if norm_price_deltas else 0.0
                except Exception:
                    v5 = float(np.var(price_deltas)) if price_deltas else 0.0

                vol_z = None
                try:
                    if len(price_deltas) > 2:
                        denom = statistics.pstdev(price_deltas)
                        if denom and denom != 0:
                            vol_z = (v5 - mean_or_none(price_deltas)) / denom
                        else:
                            vol_z = None
                except Exception:
                    vol_z = None


                row = {
                    "symbol": sym,
                    "ts": datetime.utcnow(),
                    "window_s": window_s,
                    "corr_price_oi": safe_corrcoef(price_deltas, oi_deltas),
                    "corr_price_ls": safe_corrcoef(price_deltas, ls_ratio),
                    "corr_oi_ls": safe_corrcoef(oi_deltas, ls_ratio),
                    "volatility_5s": v5,
                    "volatility_zscore": vol_z,
                    "confluence_density": sum(1 for p in price_deltas if abs(p) > 0.2) / max(1, len(price_deltas)),
                    "raw_json": {"n": len(price_deltas)},
                }
                results.append(row)
    except Exception as e:
        logger.exception(f"[compute_quant_diagnostics] failed: {e}")
    return results

async def diagnostics_loop(interval: int = 60):
    async def iteration():
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]  # could be driven from config/env
        data = await compute_quant_diagnostics(symbols)
        if data:
            try:
                await db.save_quant_diagnostics_async(data)
                logger.info(f"[Diagnostics] saved {len(data)} diagnostics rows")
            except Exception as e:
                logger.warning(f"[Diagnostics] save failed: {e}")

    await safe_loop_runner("Diagnostics", iteration, interval=interval, flush_coro=None)

# -------------------------
# Signal families
# -------------------------
async def compute_signal_families(window_s: int = 60) -> List[dict]:
    results = []
    try:
        async with db.DBConnection() as conn:
            diags = await conn.fetch("""
                SELECT DISTINCT ON (symbol) id, symbol, corr_price_oi, corr_price_ls,
                       corr_oi_ls, volatility_5s, confluence_density, ts
                FROM quant_diagnostics
                ORDER BY symbol, ts DESC
            """)
            for d in diags:
                sym = d["symbol"]
                accum = max(0.0, (d["corr_price_oi"] or 0) + (d["corr_oi_ls"] or 0)) / 2
                distrib = max(0.0, -(d["corr_price_oi"] or 0) - (d["corr_oi_ls"] or 0)) / 2
                momentum = abs(d["corr_price_oi"] or 0) * (d["volatility_5s"] or 0)
                exhaustion = max(0.0, 1 - abs(d["corr_price_oi"] or 0))
                orderflow = (d["confluence_density"] or 0) * 1.5
                divergence = max(0.0, abs((d["corr_price_ls"] or 0) - (d["corr_oi_ls"] or 0)))

                fams = {
                    "accumulation": accum,
                    "distribution": distrib,
                    "momentum": momentum,
                    "exhaustion": exhaustion,
                    "orderflow": orderflow,
                    "divergence": divergence,
                }
                for fam, val in fams.items():
                    results.append({
                        "symbol": sym,
                        "ts": d["ts"],
                        "family": fam,
                        "score": round(float(val or 0), 4),
                        "confidence": 0.8 if val and val > 0 else 0.0,
                        "diagnostics_ref": d["id"],
                        "raw_json": {"diag": dict(d)}
                    })
    except Exception as e:
        logger.exception(f"[compute_signal_families] failed: {e}")
    return results

async def signals_loop(interval: int = 60):
    async def iteration():
        data = await compute_signal_families()
        if data:
            try:
                await db.save_quant_signals_async(data)
                logger.info(f"[Signals] saved {len(data)} signal rows")
            except Exception as e:
                logger.warning(f"[Signals] save failed: {e}")
    await safe_loop_runner("Signals", iteration, interval=interval, flush_coro=None)

# -------------------------
# Confluence scores
# -------------------------
def safe_num(x):
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return 0.0
        return float(x)
    except Exception:
        return 0.0

async def compute_confluence_scores(interval_s: int = 60) -> List[dict]:
    results = []
    try:
        async with db.DBConnection() as conn:
            diags = await conn.fetch("""
                SELECT DISTINCT ON (symbol) id, symbol, volatility_5s
                FROM quant_diagnostics
                ORDER BY symbol, ts DESC
            """)
            sigs = await conn.fetch("""
                SELECT symbol, family, score
                FROM (
                    SELECT symbol, family, score,
                           ROW_NUMBER() OVER (PARTITION BY symbol, family ORDER BY ts DESC) AS rn
                    FROM quant_signals
                ) s WHERE rn=1
            """)
            sigmap = {}
            for s in sigs:
                sigmap.setdefault(s["symbol"], {})[s["family"]] = safe_num(s["score"])

            for d in diags:
                sym = d["symbol"]
                fams = sigmap.get(sym, {})

                bull = (safe_num(fams.get("accumulation")) + safe_num(fams.get("momentum"))) / 2
                bear = (safe_num(fams.get("distribution")) + safe_num(fams.get("exhaustion"))) / 2
                conf = bull - bear
                if math.isnan(conf):
                    conf = 0.0

                row = {
                    "symbol": sym,
                    "ts": datetime.utcnow(),
                    "confluence_score": round(conf, 4),
                    "bull_strength": round(bull, 4),
                    "bear_strength": round(bear, 4),
                    "volatility": safe_num(d.get("volatility_5s")),
                    "family_count": len(fams),
                    "diagnostic_ref": d["id"],
                    "raw_json": {"families": fams, "diag": dict(d)}
                }
                results.append(row)
    except Exception as e:
        logger.exception(f"[compute_confluence_scores] failed: {e}")
    return results

async def confluence_loop(interval: int = 60):
    async def iteration():
        data = await compute_confluence_scores()
        if data:
            try:
                await db.save_quant_confluence_async(data)
                logger.info(f"[Confluence] saved {len(data)} confluence rows")
            except Exception as e:
                logger.warning(f"[Confluence] save failed: {e}")
    await safe_loop_runner("Confluence", iteration, interval=interval, flush_coro=None)

# -------------------------
# Regime classification
# -------------------------
async def regime_loop(interval: int = 300):
    async def iteration():
        inserts = []
        try:
            async with db.DBConnection() as conn:
                rows = await conn.fetch("""
                    SELECT DISTINCT ON (symbol)
                        symbol, ts, confluence_score, volatility
                    FROM quant_confluence
                    ORDER BY symbol, ts DESC
                """)
            for r in rows:
                sym = r["symbol"]
                c = float(r["confluence_score"] or 0)
                v = float(r["volatility"] or 0)
                if c > 0.15 and v < 0.025:
                    regime, conf = "accumulation", c
                elif c > 0.15 and v >= 0.025:
                    regime, conf = "expansion", c
                elif c < -0.15 and v < 0.025:
                    regime, conf = "distribution", abs(c)
                elif c < -0.15 and v >= 0.025:
                    regime, conf = "exhaustion", abs(c)
                else:
                    regime, conf = "neutral", 0.1

                inserts.append({
                    "symbol": sym,
                    "ts": datetime.utcnow(),
                    "confluence_score": c,
                    "volatility": v,
                    "regime": regime,
                    "confidence": conf,
                    "raw_json": {"c": c, "v": v}
                })
            if inserts:
                await db.save_quant_regimes_async(inserts)
                logger.info(f"[Regime] saved {len(inserts)} regime rows")
        except Exception as e:
            logger.warning(f"[Regime] iteration failed: {e}")

    await safe_loop_runner("Regime", iteration, interval=interval, flush_coro=None)

# -------------------------
# Context scoring (light mode)
# -------------------------
async def context_scoring_loop(interval_s: float = 60.0):
    async def iteration():
        out_rows = []
        try:
            async with db.DBConnection() as conn:
                rows = await conn.fetch("""
                    WITH sig AS (
                        SELECT DISTINCT ON (symbol)
                            symbol, confidence AS signal_conf, ts
                        FROM quant_signals
                        ORDER BY symbol, ts DESC
                    ),
                    reg AS (
                        SELECT DISTINCT ON (symbol)
                            symbol, regime, ts
                        FROM quant_regimes
                        ORDER BY symbol, ts DESC
                    ),
                    conf AS (
                        SELECT DISTINCT ON (symbol)
                            symbol, confluence_score, bull_strength, bear_strength, ts
                        FROM quant_confluence
                        ORDER BY symbol, ts DESC
                    )
                    SELECT 
                        sig.symbol,
                        sig.signal_conf,
                        reg.regime,
                        conf.confluence_score,
                        conf.bull_strength,
                        conf.bear_strength
                    FROM sig
                    LEFT JOIN reg USING(symbol)
                    LEFT JOIN conf USING(symbol)
                """)
            now = datetime.now(timezone.utc)
            for r in rows:
                symbol = r["symbol"]
                signal_conf = float(r["signal_conf"] or 0)
                conf_score = float(r["confluence_score"] or 0)
                regime = (r["regime"] or "neutral").lower()
                if "expansion" in regime:
                    regime_boost = 1.15
                elif "distribution" in regime:
                    regime_boost = 0.9
                elif "accumulation" in regime:
                    regime_boost = 1.05
                else:
                    regime_boost = 1.0
                context_score = (0.6 * signal_conf + 0.4 * conf_score) * regime_boost
                context_score = max(0, min(context_score, 1))
                if context_score > 0.65:
                    bias = "bullish"
                elif context_score < 0.35:
                    bias = "bearish"
                else:
                    bias = "neutral"
                out_rows.append({
                    "symbol": symbol,
                    "ts": now,
                    "context_score": round(context_score, 4),
                    "bias": bias,
                    "components": {
                        "signal_conf": signal_conf,
                        "confluence_score": conf_score,
                        "regime": regime,
                        "regime_boost": regime_boost
                    }
                })
            if out_rows:
                await db.save_quant_context_scores_async(out_rows)
                logger.info(f"[ContextScoring] saved {len(out_rows)} context scores")
        except Exception as e:
            logger.warning(f"[ContextScoring] loop failed: {e}")

    await safe_loop_runner("ContextScoring", iteration, interval=interval_s, flush_coro=None)

# -------------------------
# Context trends monitor
# -------------------------
async def context_trends_loop(interval_s: float = 120.0):
    prev_biases: Dict[str, str] = {}

    async def iteration():
        nonlocal prev_biases
        transitions = []
        try:
            async with db.DBConnection() as conn:
                rows = await conn.fetch("""
                    SELECT DISTINCT ON (symbol) symbol, bias, context_score, ts
                    FROM quant_context_scores
                    ORDER BY symbol, ts DESC
                """)
            now = datetime.now(timezone.utc)
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
                        "raw_json": {"prev": prev_bias, "curr": curr_bias, "ts_ref": str(r.get("ts"))}
                    })
                prev_biases[sym] = curr_bias
            if transitions:
                await db.save_quant_context_trends_async(transitions)
                logger.info(f"[ContextTrends] detected {len(transitions)} transitions")
        except Exception as e:
            logger.warning(f"[ContextTrends] loop failed: {e}")

    await safe_loop_runner("ContextTrends", iteration, interval=interval_s, flush_coro=None)

# -------------------------
# Exports: keep names compatible with previous module
# -------------------------
__all__ = [
    "compute_quant_metrics",
    "update_quant_summary",
    "persist_5s_features",
    "run_quant_loop",
    "compute_quant_diagnostics",
    "diagnostics_loop",
    "compute_signal_families",
    "signals_loop",
    "compute_confluence_scores",
    "confluence_loop",
    "regime_loop",
    "context_scoring_loop",
    "context_trends_loop",
]
