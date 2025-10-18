# Master Roadmap v2.3 (Oct 2025) – Crypto Futures Quant Dashboard
Focus: Modular backend (async CCXTWS + Redis), Tremor 3.18+ UI, top-20 pairs scaling (dynamic volume sort), 24/7 stability (semaphore/retry/bulk), quant signals (Z-score alerts).
Principles: Async CCXTWS fstream, 30s cycle (price/OI fast, LS 30s), SQLite→Redis (P2), LocalForage offline, pytest 5s.
Workflow: Open → Scan table (top-20 quants) → Modal chart (9+ lines) → MonitorTrade (toast alerts) → EOD export. Scale: 20 pairs P1 → 200+ (virtual P3). Test: End-phase pytest + benchmarks (5s load, no blanks). No repo changes since v0.3.3 (Tremor ^3.18.7 stable).

Parallel Tracks
Architecture (backend logic, async, DB, WS) | UXUI (Tremor + Recharts, layouts) | Ops (Docker, testing, scalability)

Execution Matrix
| Focus | Diff (1-5) | Impact (⭐5) | Est Time | Prereq | Architecture Deliverables | UXUI Deliverables (Tremor Templates) | Ops Deliverables |
|-------|------------|-------------|----------|--------|---------------------------|-------------------------------------|------------------|
| Phase 1.5 Tremor Scaffold + WS Stub | 2 | 4⭐ | 2hr | None | Async scraper queue (scraper.py hook); DB cols (oi_abs_usd, global_ls_5m, ls_delta_pct, imbalance, funding); health route (req_rate/errors). [COMPLETE JSON 200, 20 pairs seeded + WS fstream] | Dashboard Layout + KPI Cards + Table (20 pairs, $OIΔ Badge); Recharts modal stub; dark theme (bg-gray-800). [COMPLETE npm run dev HMR OK + debounce] | Docker init (compose.yaml backend/node); PS unified startup (git add . ; python -m pip install -e backend[dev] ; cd frontend ; npm run dev). Target: Running Tremor dashboard showing 20 seeded pairs live. |
| Phase 1 MVP Live Table + Lines | 2 | 5⭐ | 2hr | P1.5 | Paginated apimetrics (Content-Range); WS emit (socketio.emit 'metrics_update' batch); save_metrics bulk upsert. [COMPLETE Top-20 dynamic, quants pre-calc] | Paginated Table (MetricsTable.jsx row-click modal); WS refresh hook (App.jsx debounce); TradingChart.jsx lines (price/OI/LSΔ/imbalance/funding/CVD/RSI/Z). [COMPLETE 9+ lines + toast alerts] | Pytest smoke (test_metrics.py assert len=20, oi>0, imbalance!=0); Seed script (python seed_metrics.py → DB 581+ rows). Target: Real-time metrics table auto-updating via WS (top-20 quants). |
| Phase 2 Core Timeframes + Exports | 3 | 5⭐ | 2hr | P1 WS | Tabs tf (5m-1h tf arg); Pre-calc deltas (oi_delta_pct/ls_delta_pct in db.py); Data validation (numeric/finite OILS guards before bulk). Offline LocalForage bind. [P2 TEASE CVD from klines] | Tabs Layout + Modal + Export buttons (CSV/Sheets/PDF via PapajsPDF); BarList for top movers; Spinner fetch. | Auto-refresh logs (watchdog/nodemon PS equiv); .env validation (config.py if DEV_MODE); CI stub (pytest --benchmark 5s); Logrotate (10MB x5 JSON). Target: User can switch timeframes and export snapshots (10 cols quants). |
| Phase 3 Advanced Multi-Exch + Alerts | 4 | 4⭐ | 3hr | P2 cache | Weighted global avg (Σ OI·vol / Σ vol → get_all_metrics); 1d tf; 100-200 pairs virtual; Alerts emit (LS>2 + imbalance>3% + funding>0.01% toast); Rate-limit guard (asyncio.Semaphore(8) + jitter 10 req/sec). v2.3 tease Bybit fallback WS. | Card Grid + Area Chart + Toast (LS alerts, sparkline cells); OI delta heatmap (Tremor Grid + BarList); Exchange comparison. | Redis scaling (docker-compose add redis); Pytest benchmark (10s/50 pairs); UX iteration (3 screenshots/layout via VSCode); health uptime/errors. Target: Multi-exchange weighted OI + real-time LS alerts (Z-score 2.5 spike). |
| Phase 4 Grand Trade Replay | 5 | 5⭐ | 4hr | P3 | apireplay (bisect timestamp joins); Sim toggles (Trade model); Correlation matrix top-20 OI/price batch. | DialogModal + Slider + LineChart (replay visualizer, correlation overlays); RSI/Bollinger sparkline. | Healthcheck metrics (Gunicorn + Gevent); Public demo pipeline (Vercel stub). v2.3 tease Custom alerts (localStorage thresholds). Target: Replay visualizer + simulated trades (OBV/RSI signals). |

Metrics Hierarchy (Evolution per Phase)
1. OI USD, LS ratio | Raw pulls (fetch_open_interest, LS ratio) + imbalance/funding
2. OI Δ%, LS Δ% | Precalc rolling % (Δ = (curr-prev)/prev) + CVD tease
3. Weighted Global OI | Σ(OI·vol)/Σ(vol) + Bybit weights
4. Replay Stats | Bisect timestamp joins + Z-score alerts

Tremor Template Integration
P1.5: Dashboard + KPI Cards (globals) + Table (pairs).
P2: Tabs (tf) + Modal (lines) + Export dropdown.
### Updated roadmap.md (With Git Hooks Addition)
Added row to Execution Matrix for enhancement (Diff 1, Impact 3⭐, 1hr est; ties to PS function). Drop via notepad.
P3: Card Grid (exch compare) + AreaChart (overlays) + Toasts (alerts).
P4: Dialog + Slider (replay).

Dev Enhancements (Inline Across Phases)
Unified startup PS: git add . ; python -m pip install -e backend[dev] ; cd frontend ; npm i ; npm run dev (add to README).
Logs: RotatingFileHandler (logs/app.log, 10MB x5; search emit_thread for WS); LOG_LEVEL env var (INFO/DEBUG fallback INFO); JSON structured tease.
.env: Add pydantic validate in config.py (if install pydantic; else manual checks); INTERVAL=30.
Testing: Local harness (pytest test_metrics.py --benchmark-only; assert WS emit 30s); Phase Start Command PS alias phase2 (git add . ; python -m pip install -e backend[dev] ; cd frontend ; npm run dev ; pytest -q).
UX Loops: Per phase end: Capture 3 screenshots (VSCode Ctrl+Shift+P > Developer: Take Screenshot); review dark contrast (bg-gray-800 vs text-white); Ensure KPI cards, Tables, Charts share consistent value formatting (2-dec USD, 0.00% deltas).
Frontend Hotfix: npm run lint:fix ; git add . ; npm run dev (HMR check).

v2.3: Custom thresholds (user-defined LS > X, OI Δ% > Y → localStorage config + alert toasts); Z-score OI spike (2.5).
v3.0: Data Studio Mode — load CSV/DB snapshot → dynamic Tremor replay, exportable as PDF; Bybit multi-exch full.

Docs Continuity: Scan raw Git URLs (e.g., https://raw.../quant_blueprint.md) + run gen scripts for auto-updates. See /docs/full-guide.md for embeds.