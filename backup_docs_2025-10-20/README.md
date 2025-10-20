# 📚 Crypto Futures Dashboard – Documentation Index

_Updated: 2025-10-20 | Version: v2.3 (Phase 3 – Weighted OI & Alerts)_

---

## 🧭 Core Overview

- **Project:** Real-time Crypto Futures Quant Dashboard  
- **Owner:** Lian Isaac  
- **Backend:** Flask + SQLAlchemy + SocketIO  
- **Frontend:** Vite + Tremor + Recharts  
- **Phase:** P3 – Weighted OI, Top L/S, Alerts  
- **Goal:** Detect directional bias (Z>2.5, LS>2, Imb>3%) and simulate confluence → P4 replay.

---

## 🔗 Documentation Map

| File | Description | Last Updated |
|-------|--------------|---------------|
| [continuity_state.md](continuity_state.md) | Live system snapshot (auto-updated by PowerShell) | Dynamic |
| [api_guide.md](api_guide.md) | REST + WS endpoints reference | 2025-10-18 |
| [quant_blueprint.md](quant_blueprint.md) | Architecture, metrics, and quant engineering blueprint | 2025-10-18 |
| [quant_progress_tracker.md](quant_progress_tracker.md) | Progress by phase, KPIs, and metrics evolution | 2025-10-18 |
| [roadmap.md](roadmap.md) | Execution matrix across backend, UX, ops | 2025-10-18 |
| [testing_harness.md](testing_harness.md) | Pytest and quant validation harness | 2025-10-18 |

---

## 🧩 Automation Scripts

| Script | Function | Command |
|---------|-----------|----------|
| `gen_blueprint.py` | Updates blueprint KPIs and formulas | `python docs/gen_blueprint.py` |
| `gen_tracker.py` | Updates phase progress & KPIs from DB | `python docs/gen_tracker.py` |
| `Invoke-ContinuitySnapshot` | Updates system snapshot automatically | PowerShell function |

---

## 🧮 Quant Model Summary

| Metric | Purpose | Phase |
|---------|----------|-------|
| Z-Score | Deviation/Anomaly detection | P2 |
| LS Ratio | Sentiment skew | P1 |
| Weighted OI | Volume-weighted OI strength | P3 |
| Confluence | Combined bias trigger (Z/LS/Imb) | P3 |
| Corr Replay | Rolling correlation replay sim | P4 |

---

## 🧱 Phase Summary

| Phase | Focus | Status |
|--------|--------|--------|
| 1.5 | Scaffold + WS Emit | ✅ Complete |
| 2 | Core TF + Exports | ✅ Complete |
| 3 | Weighted OI + Alerts | 🔄 In Progress |
| 4 | Replay + Corr Sim | ⏳ Planned |

---

## 🧪 Testing Hooks

Run the full validation harness:

```powershell
pytest -q
curl http://localhost:5000/api/metrics?tf=15m | jq length
python docs/gen_tracker.py
