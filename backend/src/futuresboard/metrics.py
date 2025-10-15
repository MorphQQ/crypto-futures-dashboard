import ccxt.async_support as ccxt_async
import asyncio
import aiohttp  # pip install aiohttp (add to requirements/base.txt if not)
from flask import current_app, jsonify, Blueprint
from datetime import datetime
from .scraper import send_public_request  # Single import: .scraper only (no .utils)
from .config import Config
import pathlib
import os
import time  # Add to top if missing

metrics_bp = Blueprint('metrics', __name__)


async def fetch_metrics(exchange, ccxt_symbol, raw_symbol, tf='5m'):  # Add tf param (default '5m')
    try:
        await asyncio.sleep(0.1)  # Throttle (keep your existing)
        
        # Basics: OI, Ticker, Volume (CCXT) - unchanged
        oi_data = await exchange.fetch_open_interest(ccxt_symbol)
        ticker = await exchange.fetch_ticker(ccxt_symbol)
        oi_usd = oi_data.get('openInterestAmount', 0) * ticker.get('last', 0)
        volume_24h = ticker.get('quoteVolume', 0)
        print(f"Raw OI Data for {raw_symbol}: {oi_data.get('openInterestAmount', 'N/A')} contracts")  # Your debug (use logger later)
        
        # Market Cap: CoinGecko - unchanged (keep full block)
        coingecko_ids = {
            'BTC': 'bitcoin',
            'ETH': 'ethereum',
            'SOL': 'solana',
            # ... your existing
        }
        base = raw_symbol.replace('USDT', '').upper()[:3]
        cg_id = coingecko_ids.get(base, base.lower())
        market_cap = 0
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd&include_market_cap=true"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        market_cap = data.get(cg_id, {}).get('usd_market_cap', 0)
                        print(f"Market Cap for {base} ({cg_id}): ${market_cap:,.0f}")  # Your log
                    else:
                        print(f"CoinGecko error for {cg_id}: {resp.status} - {await resp.text()}")
        except Exception as mc_e:
            print(f"Market Cap fetch error for {raw_symbol}: {mc_e}")
        
        # L/S tf-specific (updated: use tf param)
        global_ls_tf = 'N/A'
        top_ls_tf = 'N/A'
        long_account_pct = 'N/A'
        short_account_pct = 'N/A'
        top_ls_positions = 'N/A'
        try:
            _, ls_global_resp = send_public_request(
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": raw_symbol, "period": tf}  # Dynamic tf
            )
            if ls_global_resp and len(ls_global_resp) > 0:
                global_ls_tf = ls_global_resp[0].get('longShortRatio', 'N/A')
                long_account_pct = ls_global_resp[0].get('longAccount', 'N/A')
                short_account_pct = ls_global_resp[0].get('shortAccount', 'N/A')
                print(f"Global L/S for {raw_symbol} ({tf}): {global_ls_tf}")
        except Exception as ls_e:
            print(f"L/S Global {tf} error for {raw_symbol}: {ls_e}")
        
        try:
            _, ls_top_resp = send_public_request(
                "/futures/data/topLongShortAccountRatio",
                {"symbol": raw_symbol, "period": tf, "limit": 1}
            )
            print(f"Raw Top L/S Resp for {raw_symbol} ({tf}): {ls_top_resp}")
            if ls_top_resp and len(ls_top_resp) > 0:
                top_ls_tf = ls_top_resp[0].get('longShortRatio', 'N/A')
                top_ls_positions = ls_top_resp[0].get('longShortPositionRatio', 'N/A')
        except Exception as ls_e:
            print(f"L/S Top {tf} error for {raw_symbol}: {ls_e}")
        
        # OI Change % Timeframes - unchanged (your multi-tf loop; tf not used here, but could filter)
        oi_changes = {}
        timeframes = ['5m', '15m', '30m', '1h']
        for tfi in timeframes:  # tf is param, but loop uses all (keep)
            _, oi_hist_resp = send_public_request(
                "/futures/data/openInterestHist",
                {"symbol": raw_symbol, "period": tfi, "limit": 30}
            )
            oi_change_pct = 'N/A'
            if oi_hist_resp and len(oi_hist_resp) >= 2:
                current_oi = float(oi_hist_resp[0].get('openInterest', 0))
                prior_oi = float(oi_hist_resp[1].get('openInterest', 0))
                if prior_oi > 0:
                    oi_change_pct = f"{((current_oi - prior_oi) / prior_oi * 100):.2f}%"
            oi_changes[f'OI_Change_{tfi}_Pct'] = oi_change_pct
        
        # Price Change Timeframes - unchanged
        price_changes = {}
        for tfi in timeframes:
            _, price_hist_resp = send_public_request(
                "/fapi/v1/klines",
                {"symbol": raw_symbol, "interval": tfi, "limit": 2}
            )
            price_change_pct = 'N/A'
            if price_hist_resp and len(price_hist_resp) >= 2:
                current_close = float(price_hist_resp[0][4])
                prior_close = float(price_hist_resp[1][4])
                if prior_close > 0:
                    price_change_pct = f"{((current_close - prior_close) / prior_close * 100):.2f}%"
            price_changes[f'Price_Change_{tfi}_Pct'] = price_change_pct
        
        # Global L/S Timeframes - unchanged (loop all)
        ls_timeframes = {}
        for tfi in timeframes:
            _, ls_global_tf_resp = send_public_request(
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": raw_symbol, "period": tfi}
            )
            global_ls_tf_resp = ls_global_tf_resp[0].get('longShortRatio', 'N/A') if ls_global_tf_resp and len(ls_global_tf_resp) > 0 else 'N/A'
            ls_timeframes[f'Global_LS_{tfi}'] = global_ls_tf_resp
        
        # Volume Change 24h & OI 24h % - unchanged
        volume_change_24h = 'N/A'
        _, volume_hist_resp = send_public_request(
            "/fapi/v1/klines",
            {"symbol": raw_symbol, "interval": "1d", "limit": 2}
        )
        if volume_hist_resp and len(volume_hist_resp) >= 2:
            current_vol = float(volume_hist_resp[0][5])
            prior_vol = float(volume_hist_resp[1][5])
            if prior_vol > 0:
                volume_change_24h = f"{((current_vol - prior_vol) / prior_vol * 100):.2f}%"
        
        oi_change_24h = 'N/A'
        _, oi_24h_resp = send_public_request(
            "/futures/data/openInterestHist",
            {"symbol": raw_symbol, "period": "1d", "limit": 2}
        )
        if oi_24h_resp and len(oi_24h_resp) >= 2:
            current_oi24 = float(oi_24h_resp[0].get('openInterest', 0))
            prior_oi24 = float(oi_24h_resp[1].get('openInterest', 0))
            if prior_oi24 > 0:
                oi_change_24h = f"{((current_oi24 - prior_oi24) / prior_oi24 * 100):.2f}%"
        
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
            'timestamp': datetime.now().timestamp()
        }
        
        # Alert stub - unchanged
        if isinstance(result[f'Global_LS_{tf}'], (int, float)) and result[f'Global_LS_{tf}'] > 2.0:
            print(f"ALERT: {raw_symbol} Global L/S {tf} > 2.0: {result[f'Global_LS_{tf}']}")
        
        return result
    except Exception as e:
        print(f"Overall error fetching {raw_symbol}: {e}")
        return {'symbol': raw_symbol.replace('USDT', ''), 'error': str(e)}

async def get_all_metrics(tf='5m', exch='binance'):
    """Batch fetch with dynamic top-20, backoff (Phase 1). Context-free."""
    # Standalone config load (no app)
    from .config import Config
    import pathlib
    cfg_path = pathlib.Path(os.path.dirname(os.path.dirname(__file__)))  # backend root
    cfg = Config.from_config_dir(cfg_path)
    
    raw_symbols = cfg.SYMBOLS  # Always list now (from JSON/.env or default)
    
    # Exchange init (standalone—no current_app)
    exchange_class = getattr(ccxt_async, exch, ccxt_async.binance)
    exchange = exchange_class({
        'apiKey': cfg.API_KEY,
        'secret': cfg.API_SECRET,
        'enableRateLimit': True,
        'sandbox': cfg.TEST_MODE,  # True for testnet (config-driven)
        'options': {'defaultType': 'future'}
    })
    try:
        # Dynamic top-20 (sort by quoteVolume; use config as seed if <20)
        tickers = await exchange.fetch_tickers()
        usdt_pairs = [s for s in tickers if '/USDT:USDT' in s]
        sorted_ccxt_symbols = sorted(usdt_pairs, key=lambda x: tickers[x].get('quoteVolume', 0), reverse=True)[:20]
        raw_symbols = [s.replace('/USDT:USDT', 'USDT') for s in sorted_ccxt_symbols]  # Override to top-20
        
        # Convert to CCXT
        ccxt_symbols = [s.replace('USDT', '/USDT:USDT') for s in raw_symbols]
        
        # Batch with chunks/backoff (10/pair)
        results = []
        backoff = 0.2
        chunk_size = 10
        for i in range(0, len(ccxt_symbols), chunk_size):
            chunk_ccxt = ccxt_symbols[i:i+chunk_size]
            chunk_raw = raw_symbols[i:i+chunk_size]
            tasks = [fetch_metrics(exchange, cc_sym, raw_sym, tf) for cc_sym, raw_sym in zip(chunk_ccxt, chunk_raw)]
            try:
                chunk_res = await asyncio.gather(*tasks, return_exceptions=True)
                results.extend([r for r in chunk_res if not isinstance(r, Exception) and 'error' not in r])
            except ccxt_async.NetworkError as e:  # Alias match (ccxt → ccxt_async)
                if 'DDoS' in str(e) or 'rateLimit' in str(e):
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10)
                    print(f"Rate limit {exch} chunk {i//chunk_size}: {e} - retry {backoff}s")
                else:
                    raise
            await asyncio.sleep(0.5)  # Rate limit
        return results
    finally:
        await exchange.close()
        
        @metrics_bp.route('/health')
        def health():
            from .db import get_latest_metrics
            latest_count = len(get_latest_metrics(limit=1))
            return jsonify({
                'status': 'ok',
                'pairs_loaded': 20,
                'last_update': time.time(),
                'db_ping': latest_count > 0
            })