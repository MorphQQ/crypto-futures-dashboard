import ccxt.async_support as ccxt_async
import asyncio
import aiohttp  # pip install aiohttp (add to requirements/base.txt if not)
from flask import current_app, jsonify
from datetime import datetime
from .scraper import send_public_request  # Single import: .scraper only (no .utils)

async def fetch_metrics(exchange, ccxt_symbol, raw_symbol):
    try:
        await asyncio.sleep(0.1)  # Throttle
        
        # Basics: OI, Ticker, Volume (CCXT)
        oi_data = await exchange.fetch_open_interest(ccxt_symbol)
        ticker = await exchange.fetch_ticker(ccxt_symbol)
        oi_usd = oi_data.get('openInterestAmount', 0) * ticker.get('last', 0)
        volume_24h = ticker.get('quoteVolume', 0)
        current_app.logger.info(f"Raw OI Data for {raw_symbol}: {oi_data.get('openInterestAmount', 'N/A')} contracts")  # Debug
        
        # Market Cap: CoinGecko (no hardcoded fallback)
        coingecko_ids = {
            'BTC': 'bitcoin',
            'ETH': 'ethereum',
            'SOL': 'solana',
        # Add for expansion: 'ADA': 'cardano', 'XRP': 'ripple', etc.
        }
        base = raw_symbol.replace('USDT', '').upper()[:3]  # 'BTC' (trim to 3 chars)
        cg_id = coingecko_ids.get(base, base.lower())  # Fallback 'btc' if unknown
        market_cap = 0
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd&include_market_cap=true"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        market_cap = data.get(cg_id, {}).get('usd_market_cap', 0)
                        current_app.logger.info(f"Market Cap for {base} ({cg_id}): ${market_cap:,.0f}")
                    else:
                        current_app.logger.warning(f"CoinGecko error for {cg_id}: {resp.status} - {await resp.text()}")
        except Exception as mc_e:
            current_app.logger.error(f"Market Cap fetch error for {raw_symbol}: {mc_e}")
        
        # L/S 5m (isolated)
        global_ls_5m = 'N/A'
        top_ls_5m = 'N/A'
        long_account_pct = 'N/A'
        short_account_pct = 'N/A'
        top_ls_positions = 'N/A'
        try:
            _, ls_global_resp = send_public_request(
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": raw_symbol, "period": "5m"}
            )
            if ls_global_resp and len(ls_global_resp) > 0:
                global_ls_5m = ls_global_resp[0].get('longShortRatio', 'N/A')
                long_account_pct = ls_global_resp[0].get('longAccount', 'N/A')
                short_account_pct = ls_global_resp[0].get('shortAccount', 'N/A')
                current_app.logger.info(f"Global L/S for {raw_symbol}: {global_ls_5m}")  # Debug
        except Exception as ls_e:
            current_app.logger.error(f"L/S Global error for {raw_symbol}: {ls_e}")
        
        try:
            _, ls_top_resp = send_public_request(
                "/futures/data/topLongShortAccountRatio",
                {"symbol": raw_symbol, "period": "5m", "limit": 1}  # Explicit limit=1
            )
            current_app.logger.info(f"Raw Top L/S Resp for {raw_symbol}: {ls_top_resp}")  # Debug
            if ls_top_resp and len(ls_top_resp) > 0:
                top_ls_5m = ls_top_resp[0].get('longShortRatio', 'N/A')
                top_ls_positions = ls_top_resp[0].get('longShortPositionRatio', 'N/A')
        except Exception as ls_e:
            current_app.logger.error(f"L/S Top error for {raw_symbol}: {ls_e}")
        
        # OI Change % Timeframes (limit=30 for sparse data, avg last 2)
        oi_changes = {}
        timeframes = ['5m', '15m', '30m', '1h']
        for tf in timeframes:
            _, oi_hist_resp = send_public_request(
                "/futures/data/openInterestHist",
                {"symbol": raw_symbol, "period": tf, "limit": 30}
            )
            oi_change_pct = 'N/A'
            if oi_hist_resp and len(oi_hist_resp) >= 2:
                current_oi = float(oi_hist_resp[0].get('openInterest', 0))
                prior_oi = float(oi_hist_resp[1].get('openInterest', 0))
                if prior_oi > 0:
                    oi_change_pct = f"{((current_oi - prior_oi) / prior_oi * 100):.2f}%"
            oi_changes[f'OI_Change_{tf}_Pct'] = oi_change_pct
        
        # Price Change Timeframes (klines delta)
        price_changes = {}
        for tf in timeframes:
            _, price_hist_resp = send_public_request(
                "/fapi/v1/klines",
                {"symbol": raw_symbol, "interval": tf, "limit": 2}
            )
            price_change_pct = 'N/A'
            if price_hist_resp and len(price_hist_resp) >= 2:
                current_close = float(price_hist_resp[0][4])  # Close
                prior_close = float(price_hist_resp[1][4])
                if prior_close > 0:
                    price_change_pct = f"{((current_close - prior_close) / prior_close * 100):.2f}%"
            price_changes[f'Price_Change_{tf}_Pct'] = price_change_pct
        
        # Global L/S Timeframes (period param)
        ls_timeframes = {}
        for tf in timeframes:
            _, ls_global_tf_resp = send_public_request(
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": raw_symbol, "period": tf}
            )
            global_ls_tf = ls_global_tf_resp[0].get('longShortRatio', 'N/A') if ls_global_tf_resp and len(ls_global_tf_resp) > 0 else 'N/A'
            ls_timeframes[f'Global_LS_{tf}'] = global_ls_tf
        
        # Volume Change 24h (hist volume – limit=2)
        volume_change_24h = 'N/A'
        _, volume_hist_resp = send_public_request(
            "/fapi/v1/klines",
            {"symbol": raw_symbol, "interval": "1d", "limit": 2}
        )
        if volume_hist_resp and len(volume_hist_resp) >= 2:
            current_vol = float(volume_hist_resp[0][5])  # Volume
            prior_vol = float(volume_hist_resp[1][5])
            if prior_vol > 0:
                volume_change_24h = f"{((current_vol - prior_vol) / prior_vol * 100):.2f}%"
        
        # OI 24h % (from hist, period='1d')
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
            'OI_Change_24h_Pct': oi_change_24h,
            **oi_changes,  # OI_Change_5m_Pct etc.
            **price_changes,  # Price_Change_5m_Pct etc.
            'Global_LS_5m': global_ls_5m,
            **ls_timeframes,  # Global_LS_15m etc.
            'Long_Account_Pct': long_account_pct,
            'Short_Account_Pct': short_account_pct,
            'Top_LS': top_ls_5m,
            'Top_LS_Positions': top_ls_positions,
            'timestamp': datetime.now().timestamp()  # Portable
        }
        
        # Alert
        if isinstance(result['Global_LS_5m'], (int, float)) and result['Global_LS_5m'] > 2.0:
            current_app.logger.warning(f"ALERT: {raw_symbol} Global L/S 5m > 2.0: {result['Global_LS_5m']}")
        
        return result
    except Exception as e:
        current_app.logger.error(f"Overall error fetching {raw_symbol}: {e}")
        return {'symbol': raw_symbol.replace('USDT', ''), 'error': str(e)}

async def get_all_metrics():
    """Batch fetch."""
    raw_symbols = current_app.config.get('symbols', ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])
    
    # Convert to CCXT
    symbols = []
    for raw in raw_symbols:
        if raw.endswith('USDT'):
            base = raw[:-4]
            symbols.append(f"{base}/USDT:USDT")
        else:
            symbols.append(raw)
    
    exchange = ccxt_async.binance({
        'apiKey': current_app.config.get('API_KEY'),
        'secret': current_app.config.get('API_SECRET'),
        'enableRateLimit': True,
        'sandbox': current_app.config.get('sandbox', False),  # Override for live
        'options': {'defaultType': 'future'}
    })
    try:
        # Pair CCXT and raw
        tasks = [fetch_metrics(exchange, ccxt_sym, raw_sym) for ccxt_sym, raw_sym in zip(symbols, raw_symbols)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception) and 'error' not in r]
    finally:
        await exchange.close()

def add_metrics_route(app):
    @app.route('/api/metrics')
    def api_metrics():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            metrics = loop.run_until_complete(get_all_metrics())
            return jsonify(metrics)
        finally:
            loop.close()