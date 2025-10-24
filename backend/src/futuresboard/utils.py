# backend/src/futuresboard/utils.py
# ✅ Upgraded for P4.4–P5.0 Continuity
# Includes: async-safe HTTP utils, unified logging, numeric helpers, secure hashing.

from __future__ import annotations
import os
import hmac
import hashlib
import logging
import time
import aiohttp
import requests
import asyncio
from urllib.parse import urlencode
from typing import Any, Optional, Tuple, Dict, List
from collections import OrderedDict

# ==============================================================
# 🧱 LOGGER (context-safe)
# ==============================================================
try:
    from quart import current_app
except ImportError:
    current_app = None

logger = logging.getLogger("futuresboard.utils")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [UTILS] %(levelname)s: %(message)s"))
    logger.addHandler(handler)


# ==============================================================
# 🌐 HTTP HELPERS
# ==============================================================

def send_public_request(
    url_path: str,
    payload: Optional[dict] = None,
    api_base: Optional[str] = None,
    timeout: float = 5.0,
) -> Tuple[Dict[str, str], Any]:
    """
    Sync HTTP request to Binance or Bybit public API.
    Always returns (headers, json_data) even on error.
    """
    payload = payload or {}
    base = (
        api_base
        or (current_app.config.get("API_BASE_URL") if current_app else None)
        or "https://fapi.binance.com"
    )
    query = urlencode(payload, True)
    url = f"{base}{url_path}"
    if query:
        url = f"{url}?{query}"

    try:
        with requests.Session() as s:
            s.headers.update({"Content-Type": "application/json;charset=utf-8"})
            r = s.get(url, timeout=timeout)
            if r.status_code != 200:
                logger.warning(f"[HTTP] Non-200 ({r.status_code}) for {url}")
                return dict(r.headers or {}), []
            try:
                return dict(r.headers or {}), r.json()
            except Exception:
                logger.debug(f"[HTTP] Invalid JSON for {url}")
                return dict(r.headers or {}), []
    except requests.exceptions.RequestException as e:
        logger.warning(f"[HTTP] Connection error: {e}")
        return {}, []


async def send_public_request_async(
    url_path: str,
    payload: Optional[dict] = None,
    api_base: Optional[str] = None,
    timeout: float = 5.0,
) -> Tuple[Dict[str, str], Any]:
    """
    Async HTTP GET using aiohttp (for use inside async quant loops).
    Returns (headers, json_data) even on error.
    """
    payload = payload or {}
    base = api_base or "https://fapi.binance.com"
    query = urlencode(payload, True)
    url = f"{base}{url_path}"
    if query:
        url = f"{url}?{query}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout) as r:
                if r.status != 200:
                    logger.warning(f"[HTTP-ASYNC] Non-200 ({r.status}) for {url}")
                    return dict(r.headers or {}), []
                try:
                    data = await r.json()
                except Exception:
                    logger.debug(f"[HTTP-ASYNC] Invalid JSON for {url}")
                    data = []
                return dict(r.headers or {}), data
    except asyncio.TimeoutError:
        logger.warning(f"[HTTP-ASYNC] Timeout for {url}")
        return {}, []
    except Exception as e:
        logger.warning(f"[HTTP-ASYNC] Connection error: {e}")
        return {}, []


# ==============================================================
# 🔐 SIGNATURE + AUTH HELPERS
# ==============================================================

def hashing(
    query_string: str,
    exchange: str = "binance",
    timestamp: Optional[int] = None,
    api_secret: Optional[str] = None,
    api_key: Optional[str] = None,
) -> str:
    """
    Compute signature hash for Binance or Bybit using HMAC-SHA256.
    """
    secret = (
        api_secret
        or os.getenv("API_SECRET")
        or (current_app.config.get("API_SECRET") if current_app else "")
    )
    key = (
        api_key
        or os.getenv("API_KEY")
        or (current_app.config.get("API_KEY") if current_app else "")
    )
    if exchange.lower() == "bybit":
        query_string = f"{timestamp or int(time.time()*1000)}{key}5000" + query_string
    return hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_signed_query(params: dict, api_secret: str) -> str:
    """
    Given dict params, produce signed query string (Binance-compatible).
    """
    ordered = OrderedDict(sorted(params.items()))
    query = urlencode(ordered)
    signature = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"


# ==============================================================
# 🧮 NUMERIC / STATISTICAL HELPERS
# ==============================================================

def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        return float(str(x))
    except Exception:
        return None


def pct_change(a: Optional[float], b: Optional[float]) -> Optional[float]:
    try:
        if a is None or b is None or b == 0:
            return None
        return (a - b) / b * 100.0
    except Exception:
        return None


def safe_corrcoef(a: List[float], b: List[float]) -> float:
    """Compute correlation safely without NaNs or zero division."""
    import numpy as np
    a, b = list(a), list(b)
    if len(a) != len(b) or not a:
        return 0.0
    arr_a, arr_b = np.array(a, dtype=float), np.array(b, dtype=float)
    mask = np.isfinite(arr_a) & np.isfinite(arr_b)
    if not mask.any():
        return 0.0
    arr_a, arr_b = arr_a[mask], arr_b[mask]
    if arr_a.std() == 0 or arr_b.std() == 0:
        return 0.0
    return float(np.corrcoef(arr_a, arr_b)[0, 1])


def zscore(series: List[float]) -> List[Optional[float]]:
    import statistics
    clean = [x for x in series if x is not None]
    if len(clean) < 2:
        return [None] * len(series)
    mu = statistics.mean(clean)
    sigma = statistics.pstdev(clean)
    return [(x - mu) / sigma if x is not None and sigma > 0 else None for x in series]


def mean_or_none(values: List[Optional[float]]) -> Optional[float]:
    import statistics
    vs = [v for v in values if v is not None]
    return statistics.mean(vs) if vs else None


# ==============================================================
# 🕒 TIME HELPERS
# ==============================================================

def utc_ts_ms() -> int:
    """Return current UTC timestamp in milliseconds."""
    return int(time.time() * 1000)


def iso_utc_now() -> str:
    """Return ISO8601 UTC timestamp (seconds precision)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

__all__ = [
    "safe_float", "pct_change", "safe_corrcoef", "zscore", "mean_or_none",
    "send_public_request", "send_public_request_async",
    "hashing", "generate_signed_query",
    "utc_ts_ms", "iso_utc_now"
]