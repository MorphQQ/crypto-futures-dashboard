# backend/src/futuresboard/quant_engine.py
from __future__ import annotations
import asyncio
import json
import math
import statistics
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import numpy as np
from statistics import stdev, mean

from . import db
import logging

logger = logging.getLogger("futuresboard.quant_engine")
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

# small helpers
def safe_float(x):
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        return float(str(x))
    except Exception:
        return None

def pct_change(a: Optional[float], b: Optional[float]):
    """Return percent change from b -> a (i.e. (a-b)/b*100). If b is None/0 returns None."""
    try:
        if a is None or b is None:
            return None
        if b == 0:
            return None
        return (a - b) / b * 100.0
    except Exception:
        return None

def zscore(series: List[float]) -> List[Optional[float]]:
    """Return z-scores for series (same length). Returns None for missing/insufficient."""
    out = []
    clean = [x for x in series if x is not None]
    if len(clean) < 2:
        return [None] * len(series)
    mu = statistics.mean(clean)
    stdev = statistics.pstdev(clean) if len(clean) >= 2 else 0.0
    for x in series:
        if x is None or stdev == 0:
            out.append(None)
        else:
            out.append((x - mu) / stdev)
    return out

def mean_or_none(values: List[Optional[float]]):
    vs = [v for v in values if v is not None]
    if not vs:
        return None
    return statistics.mean(vs)

# --- Raw JSON parsing helpers (best-effort) ---
def parse_raw_json_field(raw):
    """
    raw may be a dict, or a JSON string; if parse fails, return None.
    We expect the common patterns:
      - {'raw': {...}, 'mark_price': '...', ...}
      - JSON strings containing nested 'raw' keys
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        # sometimes it's a JSON string with nested JSON in 'raw' -> string again
        try:
            parsed = json.loads(raw)
            # if nested 'raw' is again a string, try to load
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
    """
    From parsed raw JSON try to extract book 'bid' and 'ask' top_n volumes.
    Returns (bid_sum, ask_sum) or (None, None) if not available.
    Expected formats:
      - parsed_raw['raw'] has 'b'/'a' or 'bid'/'ask' lists of [price, size]
      - parsed_raw may include 'ask'/'bid' keys as lists
    """
    if not parsed_raw:
        return None, None
    # try multiple possible keys
    raw_field = parsed_raw.get("raw") if isinstance(parsed_raw, dict) else None
    if raw_field is None:
        raw_field = parsed_raw
    if not isinstance(raw_field, dict):
        return None, None

    bids = None
    asks = None
    # common Binance depth keys: 'b' (asks?) / 'a' / 'bid'/'ask'
    for k in ("bids", "b", "bid"):
        if k in raw_field:
            bids = raw_field[k]
            break
    for k in ("asks", "a", "ask"):
        if k in raw_field:
            asks = raw_field[k]
            break

    # sometimes asks/bids are arrays of [price,size] strings
    def sum_top(arr):
        if not isinstance(arr, list):
            return None
        s = 0.0
        n = 0
        for i, item in enumerate(arr):
            if i >= top_n:
                break
            try:
                # expect [price, size]
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
    """
    From parsed raw try to extract trade list and calculate taker buy/sell ratio.
    Return (taker_buy_count, taker_sell_count) or (None, None)
    Possible keys: 'trades', 'aggTrades', 'raw' containing trades.
    Each trade item may have 'm' (maker boolean) or 'isBuyerMaker', or 'side'.
    """
    if not parsed_raw:
        return None, None
    raw_field = parsed_raw.get("raw") if isinstance(parsed_raw, dict) else parsed_raw
    if not isinstance(raw_field, dict):
        return None, None

    # look for 'trades' or 'aggTrades' or 'recent_trades'
    trades = None
    for k in ("trades", "aggTrades", "recent_trades", "agg_trades"):
        if k in raw_field:
            trades = raw_field[k]
            break
    if trades is None:
        # sometimes trade data isn't embedded; bail
        return None, None

    buy = 0
    sell = 0
    total = 0
    for t in trades[-lookback_trades:]:
        total += 1
        # common fields: 'm' (is buyer maker), 'isBuyerMaker', 'side', 'maker'
        is_buyer_maker = None
        side = None
        if isinstance(t, dict):
            if "m" in t:
                is_buyer_maker = t.get("m")
            elif "isBuyerMaker" in t:
                is_buyer_maker = t.get("isBuyerMaker")
            if "side" in t:
                side = t.get("side")
            # determine taker buy: if is_buyer_maker is False then taker was buyer (so taker buy)
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
                # unknown - skip
                total -= 1
        else:
            total -= 1
    if total <= 0:
        return None, None
    return buy, sell

# --- Core compute function ---
async def compute_quant_metrics(limit: int = 200) -> List[Dict[str, Any]]:
    """
    Compute per-symbol quant metrics and heuristic signals.
    Returns a list of computed dictionaries (one per symbol), suitable for JSON encoding.
    This function is intentionally defensive and best-effort: when history is insufficient
    it will return None for fields it cannot compute.
    """
    try:
        raw_latest = await db.get_latest_metrics_async(limit=limit)
    except Exception as e:
        logger.exception(f"[quant_engine] failed to fetch latest metrics: {e}")
        return []

    # identify unique symbols from latest
    symbols = []
    seen = set()
    for r in raw_latest:
        sym = r.get("symbol")
        if sym and sym not in seen:
            symbols.append(sym)
            seen.add(sym)

    # concurrency: process symbols concurrently but bounded
    sem = asyncio.Semaphore(8)

    async def process_symbol(sym: str) -> Optional[Dict[str, Any]]:
        async with sem:
            try:
                # fetch per-symbol history
                rows = await db.get_metrics_by_symbol_async(sym, limit=DEFAULT_HISTORY, tf=None)
                if not rows:
                    return None
                # rows are returned newest-first per db API; reverse to chronological
                rows = list(reversed(rows))
                # extract series
                prices = [safe_float(r.get("price")) for r in rows]
                oi_usd = [safe_float(r.get("oi_usd")) for r in rows]
                vol_usd = [safe_float(r.get("vol_usd") or r.get("volume_24h")) for r in rows]
                global_ls = [safe_float(r.get("global_ls_5m") or r.get("Global_LS_5m")) for r in rows]
                top_ls_acc = [safe_float(r.get("top_ls_accounts") or r.get("Top_LS_Accounts")) for r in rows]
                top_ls_pos = [safe_float(r.get("top_ls_positions") or r.get("Top_LS_Positions")) for r in rows]
                funding = [safe_float(r.get("funding") or r.get("funding_rate") or r.get("Funding")) for r in rows]
                raw_jsons = [r.get("raw_json") or r.get("raw") or r.get("rawJson") or r.get("raw_json") for r in rows]

                # parse latest values
                latest = rows[-1]
                latest_price = safe_float(latest.get("price"))
                latest_oi = safe_float(latest.get("oi_usd"))
                latest_vol = safe_float(latest.get("vol_usd") or latest.get("volume_24h"))
                latest_global_ls = safe_float(latest.get("global_ls_5m"))
                latest_top_ls_acc = safe_float(latest.get("top_ls_accounts"))
                latest_top_ls_pos = safe_float(latest.get("top_ls_positions"))
                latest_funding = safe_float(latest.get("funding") or latest.get("funding_rate"))

                # compute percent changes using simple index offsets as time proxies
                def idx_pct_change(series, offset):
                    if len(series) <= offset or series[-1] is None or series[-1-offset] is None:
                        return None
                    return pct_change(series[-1], series[-1-offset])

                oi_ch_5 = idx_pct_change(oi_usd, 5)
                oi_ch_10 = idx_pct_change(oi_usd, 10)
                price_ch_5 = idx_pct_change(prices, 5)
                price_ch_10 = idx_pct_change(prices, 10)

                # ATR proxy: use rolling std of price returns over ATR_WINDOW
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
                    # scale to price level
                    try:
                        atr_5s = statistics.pstdev(recent_returns) * (latest_price or 1.0)
                    except Exception:
                        atr_5s = None

                # z-scores for oi, top ls, obi (computed later)
                z_oi_series = zscore([v for v in oi_usd])
                z_top_acc_series = zscore([v for v in top_ls_acc])
                # latest z values
                z_oi_latest = z_oi_series[-1] if z_oi_series else None
                z_top_acc_latest = z_top_acc_series[-1] if z_top_acc_series else None

                # parse latest raw_json for book and trades info
                parsed_latest_raw = None
                # raw_json field could be string or dict
                raw_field = raw_jsons[-1] if raw_jsons else None
                parsed_latest_raw = parse_raw_json_field(raw_field)

                # compute OBI and taker flow from parsed raw
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
                    taker_buy_ratio = taker_buy_count / max(1, taker_sell_count)
                    taker_sell_ratio = taker_sell_count / max(1, taker_buy_count)

                # compute VPI (volume-price-interest) proxy:
                # simple proxy: VPI = (taker_buy_count - taker_sell_count) * latest_vol (if counts present)
                vpi = None
                if taker_buy_count is not None and taker_sell_count is not None and latest_vol is not None:
                    vpi = (taker_buy_count - taker_sell_count) * (latest_vol or 0) / max(1, (taker_buy_count + taker_sell_count))

                # compute composite zsc using z_oi, z_top_acc, z_obi, z_funding over history if available
                # build series for obi/funding
                obi_series = []
                funding_series = []
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
                    # funding could be in row itself (we have funding list)
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

                # final zsc composite (weights as recommended)
                zsc = None
                components = []
                weights = []
                if z_oi_latest is not None:
                    components.append((z_oi_latest, 0.4))
                    weights.append(0.4)
                if z_top_acc_latest is not None:
                    components.append((z_top_acc_latest, 0.2))
                    weights.append(0.2)
                if z_obi is not None:
                    components.append((z_obi, 0.2))
                    weights.append(0.2)
                if z_funding is not None:
                    components.append((z_funding, 0.2))
                    weights.append(0.2)

                if components and sum(weights) > 0:
                    zsc = sum(val * w for val, w in components) / sum(weights)

                # compute family boolean signals with ATR-normalized thresholds
                def atr_norm(x_pct):
                    # expects percent value (e.g., 5.0) and returns normalized to ATR_5s
                    if x_pct is None or atr_5s is None or latest_price is None or latest_price == 0:
                        return None
                    try:
                        # convert pct to absolute move in price (pct/100 * price)
                        abs_move = abs(x_pct / 100.0 * latest_price)
                        return abs_move / atr_5s if atr_5s > 0 else None
                    except Exception:
                        return None

                # Accumulation (long)
                accum_ok = False
                try:
                    cond1 = oi_ch_10 is not None and atr_5s is not None and (oi_ch_10 / 100.0 * (latest_oi or 1)) / (atr_5s or 1) > 1.0 if latest_oi else False
                except Exception:
                    cond1 = False
                cond2 = price_ch_10 is not None and atr_norm(price_ch_10) is not None and abs(atr_norm(price_ch_10)) < 0.05
                cond3 = (latest_top_ls_acc is not None and latest_top_ls_acc > 1.5)
                cond4 = (obi is not None and obi > 0.55)
                cond5 = (latest_funding is not None and latest_funding < 0)
                if cond1 and cond2 and cond3 and cond4:
                    accum_ok = True

                # Distribution (short) mirror
                distrib_ok = False
                try:
                    cond1s = oi_ch_10 is not None and atr_5s is not None and (oi_ch_10 / 100.0 * (latest_oi or 1)) / (atr_5s or 1) > 1.0 if latest_oi else False
                except Exception:
                    cond1s = False
                cond2s = price_ch_10 is not None and atr_norm(price_ch_10) is not None and abs(atr_norm(price_ch_10)) < 0.05
                cond3s = (latest_top_ls_acc is not None and latest_top_ls_acc < 0.8)
                cond4s = (obi is not None and obi < 0.45)
                cond5s = (latest_funding is not None and latest_funding > 0)
                if cond1s and cond2s and cond3s and cond4s:
                    distrib_ok = True

                # Momentum (breakout)
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

                # Exhaustion / Squeeze
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

                # Orderflow (book skew) family
                orderflow_ok = False
                try:
                    # compute delta_bid_vol_5s relative to avg book depth
                    # approximate previous book top volumes if available
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

                # Divergence / Contrarian
                divergence_ok = False
                try:
                    if latest_global_ls is not None and latest_top_ls_acc is not None and latest_top_ls_acc != 0:
                        retail_vs_pro = latest_global_ls / latest_top_ls_acc
                        if retail_vs_pro > 2.5 and (latest_funding is not None and latest_funding > 0.0001) and (obi is not None and obi < 0.45):
                            divergence_ok = True
                except Exception:
                    divergence_ok = False

                # Compute a simple confidence as weighted sum of family booleans
                family_flags = {
                    "accumulation": accum_ok,
                    "distribution": distrib_ok,
                    "momentum": momentum_ok,
                    "exhaustion": exhaustion_ok,
                    "orderflow": orderflow_ok,
                    "divergence": divergence_ok
                }
                # normalize to 0..1
                score_numer = 0.0
                score_denom = 0.0
                for k, v in family_flags.items():
                    w = FAMILY_WEIGHTS.get(k, 1.0)
                    score_denom += w
                    if v:
                        score_numer += w
                confidence = score_numer / score_denom if score_denom > 0 else 0.0

                # Compose output
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
                }
                return out

            except Exception as e:
                logger.exception(f"[quant_engine] process_symbol {sym} failed: {e}")
                return None

    tasks = [asyncio.create_task(process_symbol(s)) for s in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # filter out None and exceptions
    computed = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[quant_engine] symbol processing raised: {r}")
            continue
        if r:
            computed.append(r)

    # sort by oi_usd desc if available (high exposure first)
    computed.sort(key=lambda x: (x.get("oi_usd") or 0), reverse=True)
    await db.save_quant_features_async(computed)
    return computed
    


async def update_quant_summary() -> int:
    """
    Optional: compute a light-weight summary for dashboard / persistence.
    Currently returns number of symbols computed; does not write to DB by default.
    Extend this to write into quant_summary table if desired.
    """
    try:
        data = await compute_quant_metrics(limit=200)
        # Future: write to DB.quant_summary table using db._pool or a new DB helper
        # For now we simply log and return len
        logger.debug(f"[quant_engine] update_quant_summary computed {len(data)} symbols")
        return len(data)
    except Exception as e:
        logger.exception(f"[quant_engine] update_quant_summary failed: {e}")
        return 0

# ----------------- New helper to persist results into quant_features_5s -----------------
async def persist_5s_features(computed: List[Dict[str, Any]]) -> int:
    """
    Map the computed dicts into the compact 5s schema and persist using db.save_quant_features_5s_async.
    Returns number of rows saved (as reported by DB helper).
    """
    try:
        rows = []
        for c in computed:
            # reuse timestamp if present, else now
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
        # call DB helper
        saved = await db.save_quant_features_5s_async(rows)
        return saved
    except Exception as e:
        logger.exception(f"[persist_5s_features] failed: {e}")
        return 0


# ----------------- New 5s quant loop runner -----------------
async def run_quant_loop(interval: float = 5.0):
    """
    Continuous loop to compute quant metrics at a 5s cadence and persist to quant_features_5s.
    This function is intended to run as a background task from app startup.
    """
    logger.info(f"[QuantEngine] starting 5s quant loop (interval={interval}s)")
    try:
        while True:
            try:
                computed = await compute_quant_metrics(limit=200)
                if computed:
                    # persist to HF 5s table
                    try:
                        saved = await persist_5s_features(computed)
                        logger.debug(f"[QuantEngine] persisted {saved} rows to quant_features_5s")
                    except Exception as e:
                        logger.warning(f"[QuantEngine] persist_5s_features failed: {e}")

                    # also optionally emit over socketio if app-level socket is available
                    try:
                        # avoid importing socketio at module level to prevent circular import in tests
                        from .app import sio  # local import
                        def _safe_json(data):
                            def default(o):
                                if isinstance(o, datetime):
                                    return o.isoformat()
                                return str(o)
                            return json.loads(json.dumps(data, default=default))
                        await sio.emit("quant_update_5s", {"data": _safe_json(computed), "ts": datetime.utcnow().isoformat()})
                    except Exception:
                        # non-fatal — continue
                        pass
            except asyncio.CancelledError:
                logger.info("[QuantEngine] run_quant_loop cancelled")
                raise
            except Exception as e:
                logger.exception(f"[QuantEngine] run_quant_loop iteration failed: {e}")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[QuantEngine] run_quant_loop exiting")
        raise

async def compute_quant_diagnostics(symbols: list[str], window_s: int = 60) -> list[dict]:
    """
    Pull recent 5 s data and compute rolling correlations/volatility diagnostics.
    Returns a list of dicts ready for save_quant_diagnostics_async().
    """
    global db
    results = []
    try:
        conn = await db._pool.acquire()
        for sym in symbols:
            # ~12 points per minute
            recs = await conn.fetch(
                "SELECT ts, price_change_5s_pct, oi_change_5s_pct, "
                "taker_buy_ratio, taker_sell_ratio "
                "FROM quant_features_5s WHERE symbol=$1 "
                "ORDER BY ts DESC LIMIT 120", sym
            )
            if len(recs) < 10:
                continue

            price_deltas = [r["price_change_5s_pct"] or 0 for r in recs]
            oi_deltas    = [r["oi_change_5s_pct"] or 0 for r in recs]
            ls_ratio     = [
                ((r["taker_buy_ratio"] or 0) - (r["taker_sell_ratio"] or 0))
                for r in recs
            ]

            def corr(a, b):
                if len(a) != len(b) or len(a) < 2:
                    return None
                try:
                    return float(np.corrcoef(a, b)[0, 1])
                except Exception:
                    return None

            v5 = stdev(price_deltas)
            row = {
                "symbol": sym,
                "ts": datetime.utcnow(),
                "window_s": window_s,
                "corr_price_oi": corr(price_deltas, oi_deltas),
                "corr_price_ls": corr(price_deltas, ls_ratio),
                "corr_oi_ls": corr(oi_deltas, ls_ratio),
                "volatility_5s": v5,
                "volatility_zscore": (v5 - mean(price_deltas)) / (stdev(price_deltas) or 1)
                if len(price_deltas) > 2 else None,
                "confluence_density": sum(
                    1 for p in price_deltas if abs(p) > 0.2
                ) / len(price_deltas),
                "raw_json": {"n": len(price_deltas)},
            }
            results.append(row)
        await db._pool.release(conn)
    except Exception as e:
        logger.exception(f"[compute_quant_diagnostics] failed: {e}")
    return results


async def diagnostics_loop(interval: int = 60):
    """Background loop computing diagnostics every minute."""
    logger.info(f"[Diagnostics] starting diagnostics loop (interval={interval}s)")
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    try:
        while True:
            try:
                data = await compute_quant_diagnostics(symbols)
                if data:
                    await db.save_quant_diagnostics_async(data)
                    logger.info(f"[Diagnostics] saved {len(data)} diagnostics rows")
            except Exception as e:
                logger.warning(f"[Diagnostics] iteration failed: {e}")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[Diagnostics] cancelled — exiting")
        raise
    
async def compute_signal_families(window_s: int = 60) -> list[dict]:
    """
    Derive family-level signal scores by combining diagnostics and 5 s metrics.
    Returns a list of dicts ready for save_quant_signals_async().
    """
    results = []
    try:
        conn = await db._pool.acquire()
        # fetch latest diagnostics per symbol
        diags = await conn.fetch("""
            SELECT DISTINCT ON (symbol) id, symbol, corr_price_oi, corr_price_ls,
                   corr_oi_ls, volatility_5s, confluence_density, ts
            FROM quant_diagnostics
            ORDER BY symbol, ts DESC
        """)
        for d in diags:
            sym = d["symbol"]
            # heuristic family scores
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
                    "raw_json": {"diag": dict(d)},
                })
        await db._pool.release(conn)
    except Exception as e:
        logger.exception(f"[compute_signal_families] failed: {e}")
    return results


async def signals_loop(interval: int = 60):
    """Background loop deriving signal families every minute."""
    logger.info(f"[Signals] starting signals_loop (interval={interval}s)")
    try:
        while True:
            try:
                data = await compute_signal_families()
                if data:
                    await db.save_quant_signals_async(data)
                    logger.info(f"[Signals] saved {len(data)} signal rows")
            except Exception as e:
                logger.warning(f"[Signals] iteration failed: {e}")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[Signals] cancelled — exiting")
        raise

async def compute_confluence_scores(interval_s: int = 60) -> list[dict]:
    """
    Aggregate latest signals + diagnostics into a confluence score.
    Returns list ready for save_quant_confluence_async().
    """
    results = []
    try:
        conn = await db._pool.acquire()
        # Pull latest diagnostic per symbol
        diags = await conn.fetch("""
            SELECT DISTINCT ON (symbol) id, symbol, volatility_5s
            FROM quant_diagnostics
            ORDER BY symbol, ts DESC
        """)
        # Pull latest signals
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
            sigmap.setdefault(s["symbol"], {})[s["family"]] = s["score"] or 0.0

        for d in diags:
            sym = d["symbol"]
            fams = sigmap.get(sym, {})
            bull = (fams.get("accumulation", 0) + fams.get("momentum", 0)) / 2
            bear = (fams.get("distribution", 0) + fams.get("exhaustion", 0)) / 2
            conf = bull - bear
            row = {
                "symbol": sym,
                "ts": datetime.utcnow(),
                "confluence_score": round(conf, 4),
                "bull_strength": round(bull, 4),
                "bear_strength": round(bear, 4),
                "volatility": d.get("volatility_5s"),
                "family_count": len(fams),
                "diagnostic_ref": d["id"],
                "raw_json": {"families": fams, "diag": dict(d)},
            }
            results.append(row)
        await db._pool.release(conn)
    except Exception as e:
        logger.exception(f"[compute_confluence_scores] failed: {e}")
    return results


async def confluence_loop(interval: int = 60):
    """Background loop computing composite confluence scores."""
    logger.info(f"[Confluence] starting confluence_loop (interval={interval}s)")
    try:
        while True:
            try:
                data = await compute_confluence_scores()
                if data:
                    await db.save_quant_confluence_async(data)
                    logger.info(f"[Confluence] saved {len(data)} confluence rows")
            except Exception as e:
                logger.warning(f"[Confluence] iteration failed: {e}")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[Confluence] cancelled — exiting")
        raise
