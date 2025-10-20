import ccxt.async_support as ccxt_async
import asyncio
import aiohttp
from flask import Blueprint, request, jsonify
from datetime import datetime
from .db import Session, Metric, save_metrics, get_latest_metrics  # +get_latest_metrics
from .scraper import send_public_request  # Single import: .scraper only
from .config import Config
import pathlib
import os
import time
import random  # For mock rand + jitter
import numpy as np
import pandas as pd  # New: For DataFrame weighted calc (P3)
import statistics  # For Z mean/std tease if needed

metrics_bp = Blueprint('metrics', __name__, url_prefix='/api')

# Standalone config load (no app)
cfg_path = pathlib.Path(os.path.dirname(os.path.dirname(__file__)))  # backend root
cfg = Config.from_config_dir(cfg_path.parent / "config")  # root/config
SYMBOLS = cfg.SYMBOLS  # ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] from .env/JSON
# Safe DEV_MODE (fallback True if miss; debug)
DEV_MODE = os.getenv('DEV_MODE', 'True').lower() == 'true'
print(f"Metrics cfg DEV_MODE: {DEV_MODE} (direct .env OK)")

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
    save_metrics(metrics, tf)  # Add tf param
    # New: Enrich post-save w/ deltas/Z from DB (fast query sym/tf)
    enriched = []
    for m in metrics:
        if 'error' in m: 
            enriched.append(m)
            continue
        latest = get_latest_metrics(limit=1, symbol=m['symbol'], tf=tf)  # Filter sym/tf
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
        hist = session.query(Metric).filter(Metric.symbol == symbol).order_by(Metric.timestamp.desc()).limit(24).all()  # P1: 24 points (5m=2h)
        ls_key = f'global_ls_{tf}'  # e.g., global_ls_5m
        history = []
        for m in hist:
            history.append({
                'time': int(m.timestamp.timestamp() * 1000),  # Unix ms for Recharts new Date(*1000)
                'Price': m.price,  # Add for App.jsx safeFloat(row.Price)
                'oi_abs_usd': m.oi_abs_usd,  # Align (vs oi_abs)
                ls_key: getattr(m, ls_key, None)  # Dynamic: global_ls_5m: 2.9002
            })
        print(f"History for {symbol}/{tf}: {len(history)} points")  # Debug (console on fetch)
        return jsonify(history)
    except Exception as e:
        print(f"History error for {symbol}/{tf}: {e}")
        return jsonify([]), 500
    finally:
        session.close()

@metrics_bp.route('/health')  # Phase 1: Simple status
def health():
    from .db import get_latest_metrics
    latest_count = len(get_latest_metrics(limit=1))
    return jsonify({'status': 'ok', 'pairs_loaded': len(SYMBOLS), 'last_update': time.time(), 'db_ping': latest_count > 0})

def add_metrics_route(app):
    """Lazy add for app.py (no cycle). Placeholder for Phase 4 replay."""
    pass  # Registers bp; extend w/ replay slider joins

async def get_all_metrics(tf='5m', exch='binance', limit=20, offset=0):
    """Batch fetch with dynamic top-20, backoff (Phase 1). Standalone."""
    exchange_class = getattr(ccxt_async, exch, ccxt_async.binance)
    exchange = exchange_class({
        'apiKey': cfg.API_KEY,
        'secret': cfg.API_SECRET,
        'enableRateLimit': True,
        'sandbox': cfg.TEST_MODE,
        'options': {'defaultType': 'future'}
    })
    try:
        # Dynamic top-20 (sort by quoteVolume; seed with config SYMBOLS=3)
        tickers = await exchange.fetch_tickers()
        markets = await exchange.load_markets()  # New: Active filter
        usdt_pairs = [s for s in tickers if '/USDT:USDT' in s and tickers[s].get('quoteVolume', 0) > 1e8 and markets.get(s.replace('/USDT:USDT', 'USDT'), {}).get('active', False) and markets.get(s.replace('/USDT:USDT', 'USDT'), {}).get('type') == 'future']  # Filter active futures vol>1e8
        sorted_ccxt_symbols = sorted(usdt_pairs, key=lambda x: tickers[x].get('quoteVolume', 0), reverse=True)
        if limit is not None:
            sorted_ccxt_symbols = sorted_ccxt_symbols[offset:offset+limit]
        raw_symbols = [s.replace('/USDT:USDT', 'USDT') for s in sorted_ccxt_symbols]  # Dynamic top-N, no [:len(SYMBOLS)]
        ccxt_symbols = [s.replace('USDT', '/USDT:USDT') for s in raw_symbols]
        
        # Batch chunks 10/pair w/ backoff + semaphore
        results = []
        backoff = 0.2
        chunk_size = 10
        for i in range(0, len(ccxt_symbols), chunk_size):
            chunk_ccxt = ccxt_symbols[i:i+chunk_size]
            chunk_raw = raw_symbols[i:i+chunk_size]
            async def sem_task(cc_sym, raw_sym):
                async with semaphore:  # New: 8 concurrent
                    return await fetch_metrics(exchange, cc_sym, raw_sym, tf)
            tasks = [sem_task(cc, rw) for cc, rw in zip(chunk_ccxt, chunk_raw)]
            try:
                chunk_res = await asyncio.gather(*tasks, return_exceptions=True)
                results.extend([r for r in chunk_res if not isinstance(r, Exception) and 'error' not in r])
            except ccxt_async.NetworkError as e:
                if 'DDoS' in str(e) or 'rateLimit' in str(e):
                    await asyncio.sleep(backoff + random.uniform(0.1, 0.5))  # Fix: +jitter 0.1-0.5s
                    backoff = min(backoff * 2, 10)
                    print(f"Rate limit {exch} chunk {i//chunk_size}: {e} - retry {backoff}s")
                else:
                    raise
            await asyncio.sleep(random.uniform(0.1, 0.5))  # Fix: Per-chunk jitter
        # Weighted global OI (P3: Σ(OI·vol)/Σ(vol); Bybit tease if exch=='bybit')
        if exch == 'bybit':
            print(f"Bybit fallback WS fstream for tf={tf}")
        df = pd.DataFrame(results)  # From your results list
        if len(df) > 0 and 'vol_usd' in df.columns and df['vol_usd'].sum() > 0:
            weights = df['vol_usd'] / df['vol_usd'].sum()
            weighted_oi = np.average(df['oi_abs_usd'], weights=weights)
            print(f"Weighted Global OI {tf}: ${weighted_oi / 1e9:.2f}B ({exch} weights)")
            # Add to each metric or global (for /api/metrics JSON)
            for i, row in df.iterrows():
                results[i]['weighted_oi_usd'] = weighted_oi
        # Existing return results (paginated/Content-Range)
        return results
    finally:
        await exchange.close()

async def fetch_metrics(exchange, ccxt_symbol, raw_symbol, tf='5m'):
    """Async fetch w/ early DEV mock (no attr/429; synth data)."""
    if DEV_MODE:  # Global safe
        # Early mock full (synth OI/LS/cvd/Z/imb/fund; 20 syms tease; limit=3 test)
        mocks = {
            'BTCUSDT': {'oi': 80604.697 * 60000, 'ls': 2.2206, 'cvd': 1234567, 'z': 1.20, 'imb': -2.3, 'fund': -0.01},
            'ETHUSDT': {'oi': 1448206.124 * 2500, 'ls': 1.8852, 'cvd': -876543, 'z': 0.85, 'imb': 1.2, 'fund': 0.005},
            'SOLUSDT': {'oi': 7474212.95 * 150, 'ls': 3.8852, 'cvd': 2345678, 'z': 2.10, 'imb': 3.1, 'fund': -0.02},
            # Rand fallback for 17+ (expand post-test)
        }
        mock = mocks.get(raw_symbol, {'oi': random.uniform(1e5,1e7)*100, 'ls': random.uniform(1,4), 'cvd': random.uniform(-1e6,1e6), 'z': random.uniform(-1,3), 'imb': random.uniform(-5,5), 'fund': random.uniform(-0.05,0.05)})
        ticker = {'last': random.uniform(50000,70000), 'quoteVolume': random.uniform(1e9,5e9), 'percentage': random.uniform(-1,1)}  # Synth
        print(f"DEV Early Mock full for {raw_symbol}/{tf}: OI=${mock['oi']:,.0f}, LS={mock['ls']:.4f}, CVD={mock['cvd']:,.0f}, Z={mock['z']:.2f}")
        # Build result synth (align keys; tf variants)
        ls_variants = {f'Global_LS_{tfi}': mock['ls'] + random.uniform(-0.5,0.5) for tfi in ['5m','15m','30m','1h']}
        result = {
            'symbol': raw_symbol.replace('USDT', ''),
            'Price': f"${ticker['last']:,.2f}",
            'Price_Change_24h_Pct': f"{ticker['percentage']:.2f}%",
            'Volume_24h': f"${ticker['quoteVolume']:,.0f}",
            'Volume_Change_24h_Pct': f"{random.uniform(-5,5):.2f}%",  # Synth
            'Market_Cap': f"${random.uniform(1e11,2e12):,.0f}",  # Synth
            'OI_USD': f"${mock['oi']:,.0f}",
            'oi_abs_usd': mock['oi'],
            'OI_Change_24h_Pct': f"{random.uniform(-1,1):.2f}%",
            **{f'OI_Change_{tfi}_Pct': f"{random.uniform(-0.5,0.5):.2f}%" for tfi in ['5m','15m','30m','1h']},
            **{f'Price_Change_{tfi}_Pct': f"{random.uniform(-0.2,0.2):.2f}%" for tfi in ['5m','15m','30m','1h']},
            f'Global_LS_{tf}': mock['ls'],
            **ls_variants,  # All tfs
            'Long_Account_Pct': random.uniform(50,60),  # Synth
            'Short_Account_Pct': random.uniform(40,50),
            'Top_LS': random.uniform(1.5,2.5),
            'top_ls_accounts': random.uniform(1.5,2.5),
            'Top_LS_Positions': random.uniform(0.8,1.2),
            'cvd': mock['cvd'],
            'z_ls': mock['z'],  # From enrich post-save
            'imbalance': mock['imb'],  # Fix: Synth real-ish
            'funding': mock['fund'],  # Fix: Synth real-ish
            'rsi': random.uniform(30, 70),  # New: DEV rand 30-70
            'timestamp': datetime.now().timestamp(),
            'timeframe': tf,  # New: Bind for WS filter/DB query (even if DB has col)
            'vol_usd': random.uniform(1e9,5e9)  # Synth vol for weighted (P3)
        }
        # Alert stub
        if result[f'Global_LS_{tf}'] > 2.0:
            print(f"ALERT: {raw_symbol} LS {tf} >2.0: {result[f'Global_LS_{tf}']}")
        return result
    
    try:
        await asyncio.sleep(0.1)  # Throttle
        
        # CCXT Basics: OI, Ticker, Volume (async)
        oi_data = await exchange.fetch_open_interest(ccxt_symbol)
        ticker = await exchange.fetch_ticker(ccxt_symbol)
        oi_usd = oi_data.get('openInterestAmount', 0) * ticker.get('last', 0)
        volume_24h = ticker.get('quoteVolume', 0)
        print(f"Raw OI Data for {raw_symbol}: {oi_data.get('openInterestAmount', 'N/A')} contracts")
        
        # Vol proxy mcap (quoteVolume * factor ~ mcap est; BTC $50B / $2.15T = ~43x ; avg 40 OK top-5)
        market_cap = volume_24h * 40  # Tune 30-50 ; ETH $20B / $473B = ~24x – test real
        print(f"Mcap proxy for {raw_symbol}: ${market_cap:,.0f} (vol * 40x est)")
        # Fallback if vol=0 (rare top-vol) – mocks dict top-5 only
        if market_cap == 0:
            mocks = {'BTCUSDT': 2146956539121, 'ETHUSDT': 473341878115, 'SOLUSDT': 103334191132, 'BNBUSDT': 85000000000, 'DOGEUSDT': 23000000000}  # Real-ish Oct 19, 2025 (add for syms)
            market_cap = mocks.get(raw_symbol, 1e12)  # Default 1T no random
            print(f"Post-err Mock Market Cap for {raw_symbol}: ${market_cap:,.0f}")
        
        # L/S tf-specific (send_public_request)
        global_ls_tf = 'N/A'
        top_ls_tf = 'N/A'
        long_account_pct = 'N/A'
        short_account_pct = 'N/A'
        top_ls_positions = 'N/A'
        try:
            _, ls_global_resp = send_public_request("/futures/data/globalLongShortAccountRatio", {"symbol": raw_symbol, "period": tf})
            if ls_global_resp and len(ls_global_resp) > 0:
                global_ls_tf = ls_global_resp[0].get('longShortRatio', 'N/A')
                long_account_pct = ls_global_resp[0].get('longAccount', 'N/A')
                short_account_pct = ls_global_resp[0].get('shortAccount', 'N/A')
                print(f"Global L/S for {raw_symbol} ({tf}): {global_ls_tf}")
        except Exception as ls_e:
            print(f"L/S Global {tf} error for {raw_symbol}: {ls_e}")
        
        try:
            _, ls_top_resp = send_public_request("/futures/data/topLongShortAccountRatio", {"symbol": raw_symbol, "period": tf, "limit": 1})
            if ls_top_resp and len(ls_top_resp) > 0:
                top_ls_tf = ls_top_resp[0].get('longShortRatio', 'N/A')
                top_ls_positions = ls_top_resp[0].get('longShortPositionRatio', 'N/A')
        except Exception as ls_e:
            print(f"L/S Top {tf} error for {raw_symbol}: {ls_e}")
        
        # OI Change % Timeframes (multi-tf loop)
        oi_changes = {}
        timeframes = ['5m', '15m', '30m', '1h']
        for tfi in timeframes:
            _, oi_hist_resp = send_public_request("/futures/data/openInterestHist", {"symbol": raw_symbol, "period": tfi, "limit": 50})
            oi_change_pct = 'N/A'
            if oi_hist_resp and len(oi_hist_resp) >= 2:
                current_oi = float(oi_hist_resp[0].get('openInterest', 0))
                prior_oi = float(oi_hist_resp[1].get('openInterest', 0))
                if prior_oi > 0:
                    oi_change_pct = f"{((current_oi - prior_oi) / prior_oi * 100):.2f}%"
            oi_changes[f'OI_Change_{tfi}_Pct'] = oi_change_pct
            
        # Price Change Timeframes
        price_changes = {}
        for tfi in timeframes:
            _, price_hist_resp = send_public_request("/fapi/v1/klines", {"symbol": raw_symbol, "interval": tfi, "limit": 2})
            price_change_pct = 'N/A'
            if price_hist_resp and len(price_hist_resp) >= 2:
                current_close = float(price_hist_resp[0][4])
                prior_close = float(price_hist_resp[1][4])
                if prior_close > 0:
                    price_change_pct = f"{((current_close - prior_close) / prior_close * 100):.2f}%"
            price_changes[f'Price_Change_{tfi}_Pct'] = price_change_pct
        
        # Global L/S Timeframes (all tfs)
        ls_timeframes = {}
        for tfi in timeframes:
            _, ls_global_tf_resp = send_public_request("/futures/data/globalLongShortAccountRatio", {"symbol": raw_symbol, "period": tfi})
            global_ls_tf_resp = ls_global_tf_resp[0].get('longShortRatio', 'N/A') if ls_global_tf_resp and len(ls_global_tf_resp) > 0 else 'N/A'
            ls_timeframes[f'Global_LS_{tfi}'] = global_ls_tf_resp
        
        # Volume Change 24h & OI 24h %
        volume_change_24h = 'N/A'
        _, volume_hist_resp = send_public_request("/fapi/v1/klines", {"symbol": raw_symbol, "interval": "1d", "limit": 2})
        if volume_hist_resp and len(volume_hist_resp) >= 2:
            current_vol = float(volume_hist_resp[0][5])
            prior_vol = float(volume_hist_resp[1][5])
            if prior_vol > 0:
                volume_change_24h = f"{((current_vol - prior_vol) / prior_vol * 100):.2f}%"
        
        oi_change_24h = 'N/A'
        _, oi_24h_resp = send_public_request("/futures/data/openInterestHist", {"symbol": raw_symbol, "period": "1d", "limit": 2})
        if oi_24h_resp and len(oi_24h_resp) >= 2:
            current_oi24 = float(oi_24h_resp[0].get('openInterest', 0))
            prior_oi24 = float(oi_24h_resp[1].get('openInterest', 0))
            if prior_oi24 > 0:
                oi_change_24h = f"{((current_oi24 - prior_oi24) / prior_oi24 * 100):.2f}%"
        
        # CVD: Sum vol diff (green buy +vol, red sell -vol) last 10 tf candles
        cvd = 0.0
        _, klines_resp = send_public_request("/fapi/v1/klines", {"symbol": raw_symbol, "interval": tf, "limit": 10})
        if klines_resp and len(klines_resp) >= 2:
            for kline in klines_resp[-5:]:  # Last 5
                vol = float(kline[5])  # Quote vol
                o, c = float(kline[1]), float(kline[4])
                sign = 1 if c > o else -1  # Green + , red -
                cvd += vol * sign
        print(f"CVD real {raw_symbol}/{tf}: {cvd:,.0f}")
        
        # New: RSI from klines (extend limit=50 for hist)
        _, rsi_klines_resp = send_public_request("/fapi/v1/klines", {"symbol": raw_symbol, "interval": tf, "limit": 50})
        rsi = 50.0
        if rsi_klines_resp and len(rsi_klines_resp) >= 15:
            closes = [float(k[4]) for k in rsi_klines_resp[-15:]]  # Last 15 for 14-period
            rsi = calc_rsi(np.array(closes))
            if not np.isfinite(rsi):
                rsi = 50.0
        print(f"RSI calc {raw_symbol}/{tf}: {rsi}")
        
        # New: Imbalance from depth (bid/ask vol % bias)
        _, depth_resp = send_public_request("/fapi/v1/depth", {"symbol": raw_symbol, "limit": 10})
        imbalance = 0.0
        if depth_resp:
            bids = depth_resp.get('bids', [])[:5]
            asks = depth_resp.get('asks', [])[:5]
            bid_vol = sum(float(b[1]) for b in bids)
            ask_vol = sum(float(a[1]) for a in asks)
            if (bid_vol + ask_vol) > 0:
                imbalance = ((bid_vol - ask_vol) / (bid_vol + ask_vol)) * 100
                if not np.isfinite(imbalance):
                    imbalance = 0.0
        
        # New: Funding from premiumIndex
        _, funding_resp = send_public_request("/fapi/v1/premiumIndex", {"symbol": raw_symbol})
        funding_rate = 0.0
        if funding_resp:
            funding_rate = float(funding_resp.get('lastFundingRate', 0)) * 100
            if not np.isfinite(funding_rate):
                funding_rate = 0.0
        
        result = {
            'symbol': raw_symbol.replace('USDT', ''),
            'Price': f"${ticker.get('last', 0):,.2f}",
            'Price_Change_24h_Pct': f"{ticker.get('percentage', 0):.2f}%",
            'Volume_24h': f"${volume_24h:,.0f}",
            'Volume_Change_24h_Pct': volume_change_24h,
            'Market_Cap': f"${market_cap:,.0f}",
            'OI_USD': f"${oi_usd:,.0f}",
            'oi_abs_usd': oi_usd,  # Explicit for DB (Phase 1 normalize)
            'OI_Change_24h_Pct': oi_change_24h,
            **oi_changes,
            **price_changes,
            f'Global_LS_{tf}': global_ls_tf,  # tf-specific
            **ls_timeframes,  # All tfs
            'Long_Account_Pct': long_account_pct,
            'Short_Account_Pct': short_account_pct,
            'Top_LS': top_ls_tf,
            'top_ls_accounts': top_ls_tf,  # Explicit for DB
            'Top_LS_Positions': top_ls_positions,
            'cvd': cvd,
            'z_ls': 0.0,  # From enrich post-save
            'imbalance': imbalance,  # New: Real %
            'funding': funding_rate,  # New: Real %
            'rsi': rsi,  # New: Calc
            'timestamp': datetime.now().timestamp(),
            'vol_usd': volume_24h  # New: Proxy vol from quoteVolume for weighted P3
        }
        
        # Alert stub (Phase 3 toast/beep if >2)
        if isinstance(result[f'Global_LS_{tf}'], (int, float)) and result[f'Global_LS_{tf}'] > 2.0:
            print(f"ALERT: {raw_symbol} Global L/S {tf} > 2.0: {result[f'Global_LS_{tf}']}")
        
        return result
    except Exception as e:
        print(f"Overall error fetching {raw_symbol}: {e}")
        return {'symbol': raw_symbol.replace('USDT', ''), 'error': str(e)}
