# 🧭 Continuity State – Crypto Futures Dashboard
**Last Sync:** 2025-10-20 19:00 IST  
**Phase:** P3 – Weighted OI + Top L/S + Alerts (DEV mock / live-ready)

---

## 🧩 System Overview

| Component | Status | Notes |
|------------|---------|-------|
| **Backend (Flask + SocketIO)** | ✅ Running | `/api/metrics` returning mock data |
| **Frontend (Vite + Tremor)** | ✅ Connected | WS updates received, exports working |
| **Database (SQLite)** | ✅ metrics table OK | 11 k+ rows, finite Z-scores |
| **Auto-Scraper** | ✅ Active | tf rotation 5m – 1h, queue emit confirmed |
| **Weighted OI** | ⚙️ Mock OK ($3.75 B) | Live mode pending test |
| **Top L/S Ratios** | ⚙️ Integrated | Global + Top LS across tfs visible |
| **Alerts (Z > 2.5)** | 🕐 Planned | toast + highlight triggers upcoming |

---

## 🎯 Current Objectives (P3 Phase)

1. [ ] Broadcast **Weighted OI (np.average)** to WebSocket clients  
2. [ ] Implement **L/S Tier-2 alert toasts** (Z > 2.5, LS > 2.0, Imb > 3 %)  
3. [ ] Add `/api/metrics?tf=1d` support  
4. [ ] Extend frontend modal chart → multi-tf switch  
5. [ ] Refactor scraper queue for concurrency safety (`asyncio.Semaphore`)

---

## 🧱 Core File Versions

| File | Last Edited | Hash (short) |
|------|--------------|--------------|
| app.py | 2025-10-20 | TBD |
| metrics.py | 2025-10-20 | TBD |
| db.py | 2025-10-20 | TBD |
| scraper.py | 2025-10-20 | TBD |
| App.jsx | 2025-10-20 | TBD |

*(Hashes auto-updated via `Invoke-ContinuitySnapshot`)*

---

## 🧪 Recent Test Results

**Health check:**  
→ `curl http://localhost:5000/health` → 200 `{"status":"healthy","version":"v0.3.3"}`  

**Metrics API:**  
→ `curl http://localhost:5000/api/metrics?tf=5m` → 200 OK (20 pairs)  

**Frontend:**  
→ `npm run dev` → UI table renders, WS updates < 5 s  

---

## 🧮 Key Metrics Model Fields

| Metric | Description | Source |
|---------|--------------|--------|
| `oi_abs_usd` | Open Interest in USD | `ccxt.fetch_open_interest` |
| `Global_LS_5m` | Global Long/Short Ratio | `/futures/data/globalLongShortAccountRatio` |
| `Top_LS` | Top trader long/short ratio | `/futures/data/topLongShortAccountRatio` |
| `z_ls` | LS Z-score (last 50 points) | `db.save_metrics()` |
| `imbalance` | (bid – ask)/(sum) × 100 | `/fapi/v1/depth` |
| `funding` | % funding rate | `/fapi/v1/premiumIndex` |
| `rsi` | RSI (14) from klines | `/fapi/v1/klines` |

---

## ⚙️ Environment Details

| Component | Version / Config |
|------------|-----------------|
| **OS** | Windows 10 |
| **Python** | 3.12 |
| **Node.js** | 20.x |
| **Backend** | Flask + SQLAlchemy + SocketIO |
| **Frontend** | Vite + Tremor 3.18.7 + Recharts |
| **DB Path** | `/backend/futures.db` |
| **DEV_MODE** | True |
| **Symbols** | BTCUSDT, ETHUSDT, SOLUSDT |

---

## 📚 Notes

- Finite guards enabled (`np.isfinite` for OI/LS/Z/funding/imbalance)  
- SocketIO logger active for dev  
- Scraper rotates tfs [5 m, 15 m, 30 m, 1 h]  
- Mock data enabled (`DEV_MODE=True`) for fast UI debug  
- Weighted OI = Σ(OI · vol) / Σ(vol)  
- Z-Score clipped ± 9.99 for stability  
- Safe for seeding: `python backend/seed_metrics.py --mock 20 --tf 15m`

---

_This file acts as a living snapshot of system state._  
**Auto-update via:** `Invoke-ContinuitySnapshot`
