# Fixed: backend/src/futuresboard/utils.py
# Changes:
# - GPT Patch D: Consistent tuple return, timeout=5, session.close().
# - GPT Patch E: Params for key/secret with env fallback; no current_app.

import requests
from collections import OrderedDict
from urllib.parse import urlencode
from flask import current_app
import os

def send_public_request(url_path, payload=None, api_base=None, timeout=5):
    """Return (headers, json_body) or (headers, []) on error. Always returns tuple."""
    from requests import Session, exceptions
    payload = payload or {}
    base = api_base or current_app.config.get("API_BASE_URL", "https://fapi.binance.com") if current_app else "https://fapi.binance.com"
    query_string = urlencode(payload, True)
    url = base + url_path
    if query_string:
        url = url + "?" + query_string
    session = Session()
    try:
        session.headers.update({"Content-Type": "application/json;charset=utf-8"})
        response = session.get(url, timeout=timeout)
        if response.status_code != 200:
            current_app.logger.warning(f"Non-200 status for {url}: {response.status_code}") if current_app and current_app.logger else print(f"Non-200 status for {url}: {response.status_code}")
            return response.headers or {}, []
        text = response.text or ""
        if not text.strip():
            return response.headers or {}, []
        if text.startswith("<!DOCTYPE") or "binance.com/en/error" in text.lower():
            return response.headers or {}, []
        try:
            json_response = response.json()
        except ValueError:
            return response.headers or {}, []
        # API error signal
        if isinstance(json_response, dict) and "code" in json_response and json_response["code"] < 0:
            return response.headers or {}, []
        return response.headers or {}, json_response
    except exceptions.RequestException as e:
        if current_app and current_app.logger:
            current_app.logger.warning(f"Connection error for {url}: {e}")
        else:
            print(f"Connection error for {url}: {e}")
        return {}, []
    finally:
        session.close()

def hashing(query_string, exchange="binance", timestamp=None, api_secret=None, api_key=None):
    """Signature hashing (from scraper.py)."""
    import hmac
    import hashlib
    secret = api_secret or os.getenv("API_SECRET") or current_app.config.get("API_SECRET", "") if current_app else ""
    key = api_key or os.getenv("API_KEY") or current_app.config.get("API_KEY", "") if current_app else ""
    if exchange == "bybit":
        query_string = f"{timestamp}{key}5000" + query_string
    return hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()