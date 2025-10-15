import requests
from collections import OrderedDict
from urllib.parse import urlencode
from flask import current_app

def send_public_request(url_path, payload={}, api_base=None):
    """Public API request with optional base URL (default fapi)."""
    base = api_base or current_app.config.get('API_BASE_URL', 'https://fapi.binance.com')
    query_string = urlencode(payload, True)
    url = base + url_path
    if query_string:
        url = url + "?" + query_string
    try:
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json;charset=utf-8"})
        response = session.get(url)
        print(f"DEBUG API: {url} - Status: {response.status_code}")  # Keep debug
        if response.status_code != 200:
            print(f"Non-200 status for {url}: {response.text[:100]}")
            return {}, []  # Fallback
        
        if not response.text.strip():
            print(f"Empty response for {url}")
            return {}, []
        
        # Detect HTML error page
        if response.text.startswith('<!DOCTYPE') or 'binance.com/en/error' in response.text.lower():
            print(f"HTML error page for {url}: Regional block or invalid access. Fallback empty.")
            return {}, []
        
        try:
            json_response = response.json()
        except requests.exceptions.JSONDecodeError as e:
            print(f"Non-JSON response for {url}: {response.text[:200]}")
            json_response = []  # Fallback empty list
        
        if "code" in json_response and json_response["code"] < 0:
            print(f"API error for {url}: {json_response['msg']}")
            return {}, []
        
        return response.headers, json_response
    except requests.exceptions.ConnectionError as e:
        print(f"Connection error for {url}: {e}")
        return {}, []

def hashing(query_string, exchange="binance", timestamp=None):
    """Signature hashing (from scraper.py)."""
    import hmac
    import hashlib
    if exchange == "bybit":
        query_string = f"{timestamp}{current_app.config['API_KEY']}5000" + query_string
        return hmac.new(
            bytes(current_app.config["API_SECRET"].encode("utf-8")),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    return hmac.new(
        bytes(current_app.config["API_SECRET"].encode("utf-8")),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()