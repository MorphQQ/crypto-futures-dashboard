import ccxt.async_support as ccxt_async
import asyncio
import aiohttp
from flask import Blueprint, request, jsonify
from datetime import datetime
from .db import Session, Metric, save_metrics, get_latest_metrics # +get_latest_metrics
from .scraper import send_public_request # Single import: .scraper only
from .config import Config
import pathlib
import os
import time
import random # For mock rand + jitter
import numpy as np
import pandas as pd # New: For DataFrame weighted calc (P3)
import statistics # For Z mean/std tease if needed
metrics_bp = Blueprint('metrics', __name__, url_prefix='/api')
# Standalone config load (no app)
cfg_path = pathlib.Path(os.path.dirname(os.path.dirname(__file__))) # backend root
cfg = Config.from_config_dir(cfg_path.parent / "config") # root/config
SYMBOLS = cfg.SYMBOLS # ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] from .env/JSON
# Safe DEV_MODE (fallback True if miss; debug) - Force True for P3 test (comment .env line if set False)
DEV_MODE = True  # os.getenv('DEV_MODE', 'True').lower() == 'true'
print(f"Metrics cfg DEV_MODE: {DEV_MODE} (forced True for P3 mocks/partials)")
# RSI calc (new: numpy 14-period standard)
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    rs = avg_gain / avg_loss if avg_loss != 0 else 0
    rsi = 100 - (100 / (1 + rs))
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 0
        rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)
# Semaphore global (new: P3 rate guard 8 concurrent)
semaphore = asyncio.Semaphore(8)
@metrics_bp.route('/metrics')
def api_metrics():
    tf = request.args.get('tf', '5m')
    exch = request.args.get('exch', 'binance')
    limit = int(request.args.get('limit', 20))
    offset = int(request.args.get('offset', 0))
    metrics = asyncio.run(get_all_metrics(tf, exch, limit=limit, offset=offset))
    save_metrics(metrics, tf) # Add tf param
    # New: Enrich post-save w/ deltas/Z from DB (fast query sym/tf)
    enriched = []
    for m in metrics:
        if 'error' in m:
            enriched.append(m)
            continue
        latest = get_latest_metrics(limit=1, symbol=m['symbol'], tf=tf) # Filter sym/tf
        if latest:
            m['oi_delta_pct'] = latest[0].oi_delta_pct or 0.0
            m['ls_delta_pct'] = latest[0].ls_delta_pct or 0.0
            m['z_ls'] = latest[0].z_ls or 0.0
            # Finite guard (clip Z ±10)
            if abs(m['z_ls']) > 10:
                m['z_ls'] = 10 if m['z_ls'] > 0 else -10
        else:
            m['oi_delta_pct'] = 0.0
            m['ls_delta_pct'] = 0.0
            m['z_ls'] = 0.0
        enriched.append(m)
    total = len(asyncio.run(get_all_metrics(tf, exch, limit=None, offset=0)))
    response = jsonify(enriched)
    response.headers['Content-Range'] = f'{offset}-{offset+len(enriched)-1}/{total}'
    return response
@metrics_bp.route('/<symbol>/history')
def api_history(symbol, tf='5m'):
    session = Session()
    try:
        hist = session.query(Metric).filter(Metric.symbol == symbol).order_by(Metric.timestamp.desc()).limit(24).all() # P1: 24 points (5m=2h)
        ls_key = f'global_ls_{tf}' # e.g., global_ls_5m
        history = []
        for m in hist:
            history.append({
                'time': int(m.timestamp.timestamp() * 1000), # Unix ms for Recharts new Date(*1000)
                'Price': m.price, # Add for App.jsx safeFloat(row.Price)
                'oi_abs_usd': m.oi_abs_usd, # Align (vs oi_abs)
                ls_key: getattr(m, ls_key, None) # Dynamic: global_ls_5m: 2.9002
            })
        print(f"History for {symbol}/{tf}: {len(history)} points") # Debug (console on fetch)
        return jsonify(history)
    except Exception as e:
        print(f"History error for {symbol}/{tf}: {e}")
        return jsonify([]), 500
    finally:
        session.close()
async def get_all_metrics(exch, tf='5m', limit=20, offset=0):
    """Async batch fetch w/ chunks, jitter, partials on err (P3 stable)."""
    print(f"DEBUG: get_all_metrics called tf={tf}, exch={exch}, SYMBOLS len={len(SYMBOLS)}")
    results = []
    exchange = None  # Assume get_exchange(exch) from utils.py - add if missing: def get_exchange(exch): return ccxt_async.binance({...})
    # Placeholder - add your exchange init here if not in utils
    exchange = ccxt_async.binance()  # Quick fix: init binance (add apiKey/secret if needed, but public OK)
    try:
        chunk_size = 3  # Small for debug (top-3)
        backoff = 1
        for i in range(0, len(SYMBOLS), chunk_size):
            tasks = []
            end = min(i + chunk_size, len(SYMBOLS))
            for j in range(i, end):
                raw_symbol = SYMBOLS[j]
                ccxt_symbol = raw_symbol
                tasks.append(fetch_metrics(exchange, ccxt_symbol, raw_symbol, tf))
            try:
                chunk_res = await asyncio.gather(*tasks, return_exceptions=True)
                good_res = [r for r in chunk_res if not isinstance(r, Exception) and (isinstance(r, dict) and 'error' not in r)]
                results.extend(good_res)
                exc_count = sum(1 for r in chunk_res if isinstance(r, Exception))
                err_count = sum(1 for r in chunk_res if isinstance(r, dict) and 'error' in r)
                print(f"DEBUG: Chunk {i//chunk_size} exc={exc_count}, err={err_count}, good={len(good_res)} (syms {SYMBOLS[i:end]})")
                if exc_count > 0:
                    for k, r in enumerate(chunk_res):
                        if isinstance(r, Exception):
                            print(f"DEBUG: Exc task {k} (sym {SYMBOLS[i+k]}): {type(r).__name__}: {str(r)}")
            except ccxt_async.NetworkError as e:
                if 'DDoS' in str(e) or 'rateLimit' in str(e):
                    await asyncio.sleep(backoff + random.uniform(0.1, 0.5)) # Fix: +jitter 0.1-0.5s
                    backoff = min(backoff * 2, 10)
                    print(f"Rate limit {exch} chunk {i//chunk_size}: {e} - retry {backoff}s")
                else:
                    raise
            await asyncio.sleep(random.uniform(0.1, 0.5)) # Fix: Per-chunk jitter
        # Weighted global OI (P3: Σ(OI·vol)/Σ(vol); Bybit tease if exch=='bybit')
        if exch == 'bybit':
            print(f"Bybit fallback WS fstream for tf={tf}")
        print(f"DEBUG: Raw results len={len(results)} (pre-extend/filter)")
        if len(results) > 0:
            df = pd.DataFrame(results)
            if 'vol_usd' in df.columns and df['vol_usd'].sum() > 0:
                weights = df['vol_usd'] / df['vol_usd'].sum()
                weighted_oi = np.average(df['oi_abs_usd'], weights=weights)
                print(f"Weighted Global OI {tf}: ${weighted_oi / 1e9:.2f}B ({exch} weights)")
                # Add to each metric or global (for /api/metrics JSON)
                for idx, row in df.iterrows():
                    results[idx]['weighted_oi_usd'] = weighted_oi
            print(f"DEBUG: Final results len={len(results)}, sample sym={results[0]['symbol'] if results else 'None'}")
        # Paginate if limit/offset
        if limit is not None:
            results = results[offset:offset+limit]
        return results
    finally:
        await exchange.close()
async def fetch_metrics(exchange, ccxt_symbol, raw_symbol, tf='5m'):
    """Async fetch w/ partials on err (no full drop; DEV mock fallback)."""
    if DEV_MODE: # Global safe
        # Early mock full (synth OI/LS/cvd/Z/imb/fund; expand to 20 syms for seed)
        mocks = {
            'BTCUSDT': {'oi': 80604.697 * 60000, 'ls': 2.2206, 'cvd': 1234567, 'z': 1.20, 'imb': -2.3, 'fund': -0.01},
            'ETHUSDT': {'oi': 1448206.124 * 2500, 'ls': 1.8852, 'cvd': -876543, 'z': 0.85, 'imb': 1.2, 'fund': 0.005},
            'SOLUSDT': {'oi': 7474212.95 * 150, 'ls': 3.8852, 'cvd': 2345678, 'z': 2.10, 'imb': 3.1, 'fund': -0.02},
            'BNBUSDT': {'oi': 50000 * 500, 'ls': 1.5, 'cvd': 500000, 'z': 0.5, 'imb': 0.0, 'fund': 0.0},
            'XRPUSDT': {'oi': 100000 * 0.5, 'ls': 2.0, 'cvd': 1000000, 'z': 1.0, 'imb': -1.0, 'fund': 0.01},
            # ... Add 15 more similar (e.g., DOGE: oi=1e6*0.1, ls=random.uniform(1,4), etc.)
            'DOGEUSDT': {'oi': 100000000 * 0.1, 'ls': 1.2, 'cvd': -500000, 'z': -0.5, 'imb': 2.0, 'fund': -0.005},
            'TRXUSDT': {'oi': 200000 * 0.15, 'ls': 2.5, 'cvd': 2000000, 'z': 1.5, 'imb': -0.5, 'fund': 0.0},
            'AVAXUSDT': {'oi': 50000 * 30, 'ls': 1.8, 'cvd': 800000, 'z': 0.8, 'imb': 1.5, 'fund': 0.002},
            'LINKUSDT': {'oi': 10000 * 10, 'ls': 2.1, 'cvd': -300000, 'z': -0.2, 'imb': -1.5, 'fund': -0.01},
            'MATICUSDT': {'oi': 500000 * 0.5, 'ls': 1.9, 'cvd': 400000, 'z': 0.3, 'imb': 0.5, 'fund': 0.005},
            'DOTUSDT': {'oi': 20000 * 5, 'ls': 2.3, 'cvd': 600000, 'z': 1.1, 'imb': 2.5, 'fund': 0.0},
            'LTCUSDT': {'oi': 10000 * 60, 'ls': 1.7, 'cvd': -700000, 'z': -1.0, 'imb': -2.0, 'fund': -0.003},
            'BCHUSDT': {'oi': 5000 * 300, 'ls': 2.4, 'cvd': 900000, 'z': 1.2, 'imb': 1.0, 'fund': 0.01},
            'XLMUSDT': {'oi': 1000000 * 0.1, 'ls': 1.6, 'cvd': 100000, 'z': 0.1, 'imb': -0.5, 'fund': 0.0},
            'UNIUSDT': {'oi': 10000 * 5, 'ls': 2.2, 'cvd': -200000, 'z': -0.8, 'imb': 3.0, 'fund': -0.02},
            'ATOMUSDT': {'oi': 5000 * 8, 'ls': 1.95, 'cvd': 300000, 'z': 0.9, 'imb': 0.0, 'fund': 0.004},
            'XMRUSDT': {'oi': 2000 * 150, 'ls': 2.6, 'cvd': 500000, 'z': 1.8, 'imb': -1.2, 'fund': 0.0},
            'VETUSDT': {'oi': 10000000 * 0.02, 'ls': 1.4, 'cvd': -400000, 'z': -1.2, 'imb': 1.8, 'fund': -0.001},
            'FILUSDT': {'oi': 10000 * 4, 'ls': 2.0, 'cvd': 700000, 'z': 0.6, 'imb': -0.8, 'fund': 0.006},
            'PEPEUSDT': {'oi': 1000000000 * 0.00001, 'ls': 1.3, 'cvd': 150000, 'z': -0.3, 'imb': 2.2, 'fund': 0.0},
        }
        mock = mocks.get(raw_symbol, {'oi': random.uniform(1e5,1e7)*100, 'ls': random.uniform(1,4), 'cvd': random.uniform(-1e6,1e6), 'z': random.uniform(-1,3), 'imb': random.uniform(-5,5), 'fund': random.uniform(-0.05,0.05)})
        ticker = {'last': random.uniform(50000,70000), 'quoteVolume': random.uniform(1e9,5e9), 'percentage': random.uniform(-1,1)} # Synth
        print(f"DEV Early Mock full for {raw_symbol}/{tf}: OI=${mock['oi']:,.0f}, LS={mock['ls']:.4f}, CVD={mock['cvd']:,.0f}, Z={mock['z']:.2f}")
        # Build result synth (align keys; tf variants)
        ls_variants = {f'Global_LS_{tfi}': mock['ls'] + random.uniform(-0.5,0.5) for tfi in ['5m','15m','30m','1h']}
        result = {
            'symbol': raw_symbol.replace('USDT', ''),
            'Price': f"${ticker['last']:,.2f}",
            'Price_Change_24h_Pct': f"{ticker['percentage']:.2f}%",
            'Volume_24h': f"${ticker['quoteVolume']:,.0f}",
            'Volume_Change_24h_Pct': f"{random.uniform(-5,5):.2f}%", # Synth
            'Market_Cap': f"${random.uniform(1e11,2e12):,.0f}", # Synth
            'OI_USD': f"${mock['oi']:,.0f}",
            'oi_abs_usd': mock['oi'],
            'OI_Change_24h_Pct': f"{random.uniform(-1,1):.2f}%",
            **{f'OI_Change_{tfi}_Pct': f"{random.uniform(-0.5,0.5):.2f}%" for tfi in ['5m','15m','30m','1h']},
            **{f'Price_Change_{tfi}_Pct': f"{random.uniform(-0.2,0.2):.2f}%" for tfi in ['5m','15m','30m','1h']},
            f'Global_LS_{tf}': mock['ls'],
            **ls_variants, # All tfs
            'Long_Account_Pct': random.uniform(50,60), # Synth
            'Short_Account_Pct': random.uniform(40,50),
            'Top_LS': random.uniform(1.5,2.5),
            'top_ls_accounts': random.uniform(1.5,2.5),
            'Top_LS_Positions': random.uniform(0.8,1.2),
            'cvd': mock['cvd'],
            'z_ls': mock['z'], # From enrich post-save
            'imbalance': mock['imb'], # Fix: Synth real-ish
            'funding': mock['fund'], # Fix: Synth real-ish
            'rsi': random.uniform(30, 70), # New: DEV rand 30-70
            'timestamp': datetime.now().timestamp(),
            'timeframe': tf, # New: Bind for WS filter/DB query (even if DB has col)
            'vol_usd': random.uniform(1e9,5e9) # Synth vol for weighted (P3)
        }
        # Alert stub
        if result[f'Global_LS_{tf}'] > 2.0:
            print(f"ALERT: {raw_symbol} LS {tf} >2.0: {result[f'Global_LS_{tf}']}")
        return result
   
    try:
        await asyncio.sleep(0.1) # Throttle
       
        # CCXT Basics: OI, Ticker, Volume (async) - partial on fail
        oi_data = {'openInterestAmount': 0}
        try:
            oi_data = await exchange.fetch_open_interest(ccxt_symbol)
        except Exception as e:
            print(f"OI fetch error {raw_symbol}: {type(e).__name__}: {str(e)}")
        ticker = {'last': 0, 'quoteVolume': 0, 'percentage': 0}
        try:
            ticker = await exchange.fetch_ticker(ccxt_symbol)
        except Exception as e:
            print(f"Ticker fetch error {raw_symbol}: {type(e).__name__}: {str(e)}")
        oi_usd = oi_data.get('openInterestAmount', 0) * ticker.get('last', 0)
        volume_24h = ticker.get('quoteVolume', 0)
        print(f"Raw OI Data for {raw_symbol}: {oi_data.get('openInterestAmount', 'N/A')} contracts")
       
        # Vol proxy mcap (quoteVolume * factor ~ mcap est; BTC $50B / $2.15T = ~43x ; avg 40 OK top-5)
        market_cap = volume_24h * 40 # Tune 30-50 ; ETH $20B / $473B = ~24x – test real
        print(f"Mcap proxy for {raw_symbol}: ${market_cap:,.0f} (vol * 40x est)")
        # Fallback if vol=0 (rare top-vol) – mocks dict top-5 only
        if market_cap == 0:
            mocks = {'BTCUSDT': 2146956539121, 'ETHUSDT': 473341878115, 'SOLUSDT': 103334191132, 'BNBUSDT': 85000000000, 'DOGEUSDT': 23000000000} # Real-ish Oct 19, 2025 (add for syms)
            market_cap = mocks.get(raw_symbol, 1e12) # Default 1T no random
            print(f"Post-err Mock Market Cap for {raw_symbol}: ${market_cap:,.0f}")
       
        # L/S tf-specific (send_public_request) - partial on fail
        global_ls_tf = 'N/A'
        top_ls_tf = 'N/A'
        long_account_pct = 'N/A'
        short_account_pct = 'N/A'
        top_ls_positions = 'N/A'
        try:
            # Global account ratio for tf
            _, account_resp = send_public_request("/futures/data/globalLongShortAccountRatio", {"symbol": raw_symbol, "period": tf})
            if account_resp and len(account_resp) > 0:
                long_account_pct = account_resp[0].get('longAccount', 50)
                short_account_pct = 100 - long_account_pct
                global_ls_tf = account_resp[0].get('longShortRatio', 'N/A')
        except Exception as e:
            print(f"Account LS error {raw_symbol}/{tf}: {type(e).__name__}: {str(e)}")
        try:
            # Top position ratio for tf
            _, top_pos_resp = send_public_request("/futures/data/topLongShortPositionRatio", {"symbol": raw_symbol, "period": tf})
            if top_pos_resp and len(top_pos_resp) > 0:
                top_ls_tf = top_pos_resp[0].get('longShortRatio', 'N/A')
                top_ls_positions = top_ls_tf  # Align
        except Exception as e:
            print(f"Top pos LS error {raw_symbol}/{tf}: {type(e).__name__}: {str(e)}")
       
        timeframes = ['5m', '15m', '30m', '1h']
        ls_timeframes = {}
        price_changes = {}
        oi_changes = {}
        for tfi in timeframes:
            # Global LS all tfs
            try:
                _, ls_global_tf_resp = send_public_request("/futures/data/globalLongShortAccountRatio", {"symbol": raw_symbol, "period": tfi})
                global_ls_tf_resp = ls_global_tf_resp[0].get('longShortRatio', 'N/A') if ls_global_tf_resp and len(ls_global_tf_resp) > 0 else 'N/A'
                ls_timeframes[f'Global_LS_{tfi}'] = global_ls_tf_resp
            except Exception as e:
                print(f"Global LS error {raw_symbol}/{tfi}: {type(e).__name__}: {str(e)}")
                ls_timeframes[f'Global_LS_{tfi}'] = 'N/A'
            # Price change tf
            try:
                _, price_resp = send_public_request("/fapi/v1/klines", {"symbol": raw_symbol, "interval": tfi, "limit": 2})
                if price_resp and len(price_resp) >= 2:
                    current_price = float(price_resp[0][4])
                    prior_price = float(price_resp[1][4])
                    if prior_price > 0:
                        pct = ((current_price - prior_price) / prior_price * 100)
                        price_changes[f'Price_Change_{tfi}_Pct'] = f"{pct:.2f}%"
            except Exception as e:
                print(f"Price change error {raw_symbol}/{tfi}: {type(e).__name__}: {str(e)}")
            # OI change tf
            try:
                _, oi_tf_resp = send_public_request("/futures/data/openInterestHist", {"symbol": raw_symbol, "period": tfi, "limit": 2})
                if oi_tf_resp and len(oi_tf_resp) >= 2:
                    current_oi = float(oi_tf_resp[0].get('openInterest', 0))
                    prior_oi = float(oi_tf_resp[1].get('openInterest', 0))
                    if prior_oi > 0:
                        pct = ((current_oi - prior_oi) / prior_oi * 100)
                        oi_changes[f'OI_Change_{tfi}_Pct'] = f"{pct:.2f}%"
            except Exception as e:
                print(f"OI change error {raw_symbol}/{tfi}: {type(e).__name__}: {str(e)}")
       
        # Volume Change 24h & OI 24h %
        volume_change_24h = 'N/A'
        try:
            _, volume_hist_resp = send_public_request("/fapi/v1/klines", {"symbol": raw_symbol, "interval": "1d", "limit": 2})
            if volume_hist_resp and len(volume_hist_resp) >= 2:
                current_vol = float(volume_hist_resp[0][5])
                prior_vol = float(volume_hist_resp[1][5])
                if prior_vol > 0:
                    volume_change_24h = f"{((current_vol - prior_vol) / prior_vol * 100):.2f}%"
        except Exception as e:
            print(f"Vol 24h error {raw_symbol}: {type(e).__name__}: {str(e)}")
       
        oi_change_24h = 'N/A'
        try:
            _, oi_24h_resp = send_public_request("/futures/data/openInterestHist", {"symbol": raw_symbol, "period": "1d", "limit": 2})
            if oi_24h_resp and len(oi_24h_resp) >= 2:
                current_oi24 = float(oi_24h_resp[0].get('openInterest', 0))
                prior_oi24 = float(oi_24h_resp[1].get('openInterest', 0))
                if prior_oi24 > 0:
                    oi_change_24h = f"{((current_oi24 - prior_oi24) / prior_oi24 * 100):.2f}%"
        except Exception as e:
            print(f"OI 24h error {raw_symbol}: {type(e).__name__}: {str(e)}")
       
        # CVD: Sum vol diff (green buy +vol, red sell -vol) last 10 tf candles
        cvd = 0.0
        try:
            _, klines_resp = send_public_request("/fapi/v1/klines", {"symbol": raw_symbol, "interval": tf, "limit": 10})
            if klines_resp and len(klines_resp) >= 2:
                for kline in klines_resp[-5:]: # Last 5
                    vol = float(kline[5]) # Quote vol
                    o, c = float(kline[1]), float(kline[4])
                    sign = 1 if c > o else -1 # Green + , red -
                    cvd += vol * sign
        except Exception as e:
            print(f"CVD klines error {raw_symbol}/{tf}: {type(e).__name__}: {str(e)}")
        print(f"CVD real {raw_symbol}/{tf}: {cvd:,.0f}")
       
        # New: RSI from klines (extend limit=50 for hist)
        rsi = 50.0
        try:
            _, rsi_klines_resp = send_public_request("/fapi/v1/klines", {"symbol": raw_symbol, "interval": tf, "limit": 50})
            if rsi_klines_resp and len(rsi_klines_resp) >= 15:
                closes = [float(k[4]) for k in rsi_klines_resp[-15:]] # Last 15 for 14-period
                rsi = calc_rsi(np.array(closes))
                if not np.isfinite(rsi):
                    rsi = 50.0
        except Exception as e:
            print(f"RSI klines error {raw_symbol}/{tf}: {type(e).__name__}: {str(e)}")
        print(f"RSI calc {raw_symbol}/{tf}: {rsi}")
       
        # New: Imbalance from depth (bid/ask vol % bias)
        imbalance = 0.0
        try:
            _, depth_resp = send_public_request("/fapi/v1/depth", {"symbol": raw_symbol, "limit": 10})
            if depth_resp:
                bids = depth_resp.get('bids', [])[:5]
                asks = depth_resp.get('asks', [])[:5]
                bid_vol = sum(float(b[1]) for b in bids)
                ask_vol = sum(float(a[1]) for a in asks)
                if (bid_vol + ask_vol) > 0:
                    imbalance = ((bid_vol - ask_vol) / (bid_vol + ask_vol)) * 100
                    if not np.isfinite(imbalance):
                        imbalance = 0.0
        except Exception as e:
            print(f"Depth imbalance error {raw_symbol}: {type(e).__name__}: {str(e)}")
       
        # New: Funding from premiumIndex
        funding_rate = 0.0
        try:
            _, funding_resp = send_public_request("/fapi/v1/premiumIndex", {"symbol": raw_symbol})
            if funding_resp:
                funding_rate = float(funding_resp.get('lastFundingRate', 0)) * 100
                if not np.isfinite(funding_rate):
                    funding_rate = 0.0
        except Exception as e:
            print(f"Funding error {raw_symbol}: {type(e).__name__}: {str(e)}")
       
        result = {
            'symbol': raw_symbol.replace('USDT', ''),
            'Price': f"${ticker.get('last', 0):,.2f}",
            'Price_Change_24h_Pct': f"{ticker.get('percentage', 0):.2f}%",
            'Volume_24h': f"${volume_24h:,.0f}",
            'Volume_Change_24h_Pct': volume_change_24h,
            'Market_Cap': f"${market_cap:,.0f}",
            'OI_USD': f"${oi_usd:,.0f}",
            'oi_abs_usd': oi_usd, # Explicit for DB (Phase 1 normalize)
            'OI_Change_24h_Pct': oi_change_24h,
            **oi_changes,
            **price_changes,
            f'Global_LS_{tf}': global_ls_tf, # tf-specific
            **ls_timeframes, # All tfs
            'Long_Account_Pct': long_account_pct,
            'Short_Account_Pct': short_account_pct,
            'Top_LS': top_ls_tf,
            'top_ls_accounts': top_ls_tf, # Explicit for DB
            'Top_LS_Positions': top_ls_positions,
            'cvd': cvd,
            'z_ls': 0.0, # From enrich post-save
            'imbalance': imbalance, # New: Real %
            'funding': funding_rate, # New: Real %
            'rsi': rsi, # New: Calc
            'timestamp': datetime.now().timestamp(),
            'vol_usd': volume_24h # New: Proxy vol from quoteVolume for weighted P3
        }
       
        # Alert stub (Phase 3 toast/beep if >2)
        if isinstance(result[f'Global_LS_{tf}'], (int, float)) and result[f'Global_LS_{tf}'] > 2.0:
            print(f"ALERT: {raw_symbol} Global L/S {tf} > 2.0: {result[f'Global_LS_{tf}']}")
       
        return result
    except Exception as e:
        print(f"Overall error fetching {raw_symbol}: {type(e).__name__}: {str(e)}")
        return {'symbol': raw_symbol.replace('USDT', ''), 'error': str(e)}
    
def add_metrics_route(app):
    """Register metrics_bp to app (for app.py init)."""
    app.register_blueprint(metrics_bp)
    print("Metrics routes registered (/api/metrics, /<sym>/history)")