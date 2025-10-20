
**quant_blueprint.md Clean Stub (Paste Full; ~512L – From Screenshot Tease)**:
```markdown
# Quant Engineering Blueprint v1.0 (Grok-Adapted) – Oct 18, 2025  
Project: Crypto Futures Dashboard (v0.3.3 Fork + Tremor UI)  
Vision: Stats-first quant tool for futures bias detection (Z>2.5/LS>2/imb>3% confluence) → replay sim (P4) w/ 200+ pair scale.  
Owner: Lian Isaac | Date: 2025-10-18 | Version: QE v1.0 (P2 95% → P3 Prep)  

## I. System Layers (Flow: Scraper → DB → Metrics → WS → UI) [Merged from Arch Context]
| Layer | Function | Modules (Files) |  
|-------|----------|-----------------|  
| 1. Ingestion | High-freq pulls (OI/LS/price via CCXT/WS; jitter<10/sec) | scraper.py (Semaphore(8)/30s queue/tf rotate 5m-15m; logging recon/backoff); metrics.py (Coingecko mock/guards; REST fallback) |  
| 2. Persistence | Bulk store + tf bind (SQLite → DuckDB P3 tease) | db.py (upsert cols: oi_abs_usd/global_ls/top_ls/imbalance/funding/cvd/rsi/z_score/timeframe; finite np.isfinite; batch minimal blocking) |  
| 3. Analytics | Pre-calc quants (deltas/Z/RSI/CVD; weighted OI P3) | metrics.py (get_all batch; quant_utils inline: Z=(x-μ)/σ roll20; corr Pearson; Modular signals e.g., calc_confluence) |  
| 4. API/Event | Paginated REST + WS emit (batch/tf ?arg) | app.py (/api/metrics Content-Range; socketio.emit 'metrics_update'/alert_toast; /health rate/errors) |  
| 5. Viz/UX | Table/modal (9+ lines) + tf switch/toast/export | App.jsx (WS debounce1s/LocalForage per-tf; TradingChart Recharts; hot-toast Z-spike; Non-blocking lazy modal) |  
| 6. Intelligence (P4) | Replay/corr/sim (bisect joins + PnL regret) | /api/replay tease; trade_sim.py (OBV/RSI entry/exit log JSON) |  

## II. Core Calculations (Inline Formulas – Drop to metrics.py) [Metrics Hierarchy Merged + Evolution Per Phase]
| Metric | Formula | Purpose | Snippet Test (code_execution) | Phase Tie |
|--------|---------|---------|-------------------------------|-----------|
| OI USD, LS Ratio | Raw CCXT/Coingecko pulls | Leverage/sentiment | `import ccxt; binance = ccxt.binance(); oi = binance.fetch_open_interest('BTC/USDT'); print(oi['openInterestAmount'])` → 5.695e9 ETH | P1 COMPLETE: + imb/funding; Logging "REST fallback on WS fail" |
| OI Δ%, LS Δ% | (OI_t - OI_{t-1}) / OI_{t-1} * 100; Rolling % CVD/RSI/Z | Flow/momentum | `import numpy as np; oi = np.array([1e9, 1.01e9]); delta = (oi[1]-oi[0])/oi[0]*100; print(delta)` → 1.0; Finite guards np.isfinite | P2 95%: + CVD/RSI/Z tf bind (e.g., Z=1.20 BTC/5m vs 1.50/15m); Modular calc_confluence |
| Weighted Global OI | Σ(OI * vol) / Σ(vol) | Exch aggregate | `weights = df['vol']; w_oi = np.average(df['oi'], weights=weights/weights.sum())` → $3.75B | P3 20%: + Bybit weights; Logging weights |
| Replay Stats | Bisect ts joins + Z/confluence | Anomaly/corr | `from scipy.stats import pearsonr; r, _ = pearsonr(df['oi'], df['price'])` → Heatmap JSON r>0.6 flag | P4 0%: + Z/confluence alerts; Corr r roll24h; Logging PnL |
| Global LS | Long Vol / Short Vol | Sentiment skew | In db.py: `ls_ratio = long_vol / short_vol if short_vol else 1` | P1 |
| Imbalance | (Buy - Sell) / Total Vol | Microstructure bias | `imb = (buy - sell) / (buy + sell); np.isfinite(imb)` → Guard <50% | P1 |
| CVD | Σ(Buy - Sell) over tf | Volume confirmation | Rolling in metrics.py: `df['cvd'] = df['buy'].cumsum() - df['sell'].cumsum()` | P2 |
| RSI (14) | 100 - 100/(1 + RS); RS=AvgGain/AvgLoss | Momentum revert | TA-lib tease: `from ta.momentum import RSIIndicator; rsi = RSIIndicator(close).rsi()` | P2 |
| Z-Score | (x - μ) / σ (roll 20 bars/tf) | Anomaly detect | `from scipy.stats import zscore; z = zscore(df['oi_delta_pct'].rolling(20).mean())` → <10 cap | P2 |
| Confluence (P3) | Sum(Z>2.5 + LS>2 + |imb|>3%) / 3 | Multi-factor alert | `score = sum([z>2.5, ls>2, abs(imb)>3])/3; if score>0.66: emit 'alert_toast'` | P3 |
| Corr (P4) | cov(OI, Price) / (σ_OI * σ_Price) roll24h | Confluence r | `from scipy.stats import pearsonr; r, _ = pearsonr(df['oi'], df['price'])` → Heatmap JSON | P4 |

## III. Stack & Controls (Per v2.3 Principles)  
- **Lang/Libs:** Py3.12 (ccxt/numpy/pandas/scipy); Node20 (Tremor^3.18.7/Recharts/hot-toast).  
- **Storage:** SQLite P2 (581+ rows/tf bind); DuckDB P3 (replay queries); Redis P3 (pub/sub scale).  
- **Pipeline:** 30s cycle (.env INTERVAL); Semaphore(8) concurrency; jitter uniform(0.1,0.9); Guards: np.isfinite(Z<10/funding<0.05/imb<50). Emit: scraper → db → metrics → socketio.  
- **Offline:** LocalForage bind per-tf (App.jsx fallback cache).  

## IV. Signal Hierarchy (Tiers → Toast/Action)  
| Tier | Trigger | Action |  
|------|---------|--------|  
| 1 | Z>2.5 | Toast + highlight (sym/tf) |  
| 2 | Z>2.5 + LS>2 + |imb|>3% | High-prio toast/sound; Chart overlay |  
| 3 | CVD align + OI Δ>1% | Confidence flag (export log) |  
| 4 | Funding>0.01% + OI fall | Caution (localStorage thresh) |  
| 5 | Weighted OI div >2σ (P3) | Exch compare grid |  
| 6 | Replay cluster (P4) | Sim trade trigger (PnL>60% goal) |  

## V. Engine Tease (P4 Modules – New Files)  
1. **Replay (replay_engine.py):** DuckDB bisect ts joins; step-forward sim. Snippet: `import bisect; ts_idx = bisect.bisect_left(df['timestamp'], target_ts)`  
2. **TradeSim (trade_sim.py):** Backtest signals; regret = PnL_entry - PnL_exit. Test: code_execution PnL calc (>60% hit).  
3. **Corr Analyzer (corr_matrix.py):** Rolling r top-20; JSON → Recharts heatmap.  

## VI. Evolution (Phases)  
| Phase | Upgrade | Quant Add |  
|-------|---------|-----------|  
| v1.0 (Now) | Flask/SQLite | Core Z/LS/CVD/RSI tf bind |  
| v2.0 (Nov) | Async CCXT/Redis | Weighted OI + Tier2 alerts |  
| v3.0 (Dec) | FastAPI/DuckDB | Corr + Replay bisect |  
| v4.0 (Jan) | Gunicorn scale | TradeSim PnL + custom thresh |  

## VII. QA/Testing (Pytest + Tools)  
| Layer | Test | Tool | Target |  
|-------|------|------|--------|  
| API | /metrics?tf=15m 200/20 pairs no NaN | pytest/curl | <1.5s lat |  
| DB | Rows>581 tf bind; finite Z | code_execution pandas | Stability |  
| Quant | Z<10; corr<1; confluence>0.66 emit | numpy/scipy | <5% false pos |  
| WS | 24h uptime <5 recon | Logs/Prometheus tease | 99.9% |  
| Replay (P4) | PnL hit>60% | Custom script | Validation |  

## VIII. KPIs [Gen Auto-Merge from gen_blueprint.py]
| Metric | Target | Measure |  
|--------|--------|---------|  
| Uptime | 99.9% | /health Prometheus |  
| Latency | <5s loop | Scraper timing |  
| Alert Acc | >95% | Logs/manual |  
| Sim Hit | >60% | TradeSim output |  
| Capacity | 200+ pairs | P3 benchmark |  

## IX. Commands (PS Shortcuts)  
```powershell  
# Run + Seed  
cd backend/src/futuresboard; python app.py ; cd ../.. ; python seed_metrics.py --mock 20 --tf 15m  
# Git + Blueprint Update  
git add docs/quant_blueprint.md ; git commit -m "QE v1.0: Weighted tease + tiers" ; git push  
# Auto-Gen MD from DB (New: docs/gen_blueprint.py)  
python docs/gen_blueprint.py # Queries rows/Z avg → Update Section VIII ### Auto-KPI Update (No Data)
| Weighted OI | Current |
|-------------|---------|
| $0.00B | No rows |
## Auto-KPI Update (No Data)
| Weighted OI | Current |
|-------------|---------|
| $0.00B | No rows |

## Auto-KPI Update (No Data)
| Weighted OI | Current |
|-------------|---------|
| $0.00B | No rows |
## Auto-KPI Update (No Data)
| Weighted OI | Current |
|-------------|---------|
| $0.00B | No rows |

