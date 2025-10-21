# Architecture Overview Ã¢â‚¬â€œ Crypto Futures Dashboard

## 1. System Summary

**Backend:** futuresboard
**Frontend:** React + Tailwind + Tremor dashboard  
**Data Source:** Real-time crypto futures market feeds  
**Persistence:** SQLite / SQLAlchemy  
**Automation Layer:** PowerShell Continuity Framework  

---

## 2. Component Breakdown

| Layer | Technology | Description |
|-------|-------------|-------------|
| **Backend (API)** | Python 3.13 / Flask | Serves live market metrics and quant summaries |
| **WebSocket Service** | SocketIO | Pushes streaming updates to the frontend |
| **Database** | SQLite | Stores tick data, Z-scores, and historical stats |
| **Frontend UI** | React + Tailwind + Tremor | Displays metrics, alerts, and performance graphs |
| **Automation** | PowerShell | Handles snapshots, sync, and phase tracking |
| **Version Control** | Git + Safe-GitPush | Commits all docs and states to GitHub |

---

## 3. Continuity Data Flow

```text
Invoke-ContinuitySnapshot
     Ã¢â€â€š
     Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ continuity_state.json
     Ã¢â€â€š
     Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ project_context_v3.json
     Ã¢â€â€š
     Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ Invoke-StatusReport
     Ã¢â€â€š
     Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ status_report.md Ã¢â€ â€™ Safe-GitPush Ã¢â€ â€™ GitHub
```

Each layer reflects the projectÃ¢â‚¬â„¢s **live backend status** and **phase tag**.

---

## 4. Current Phase

**Phase:** P3.6 - UTF8 Logging + QuantSummary Stable
**Backend:** futuresboard
**Maintainer:** Lian Isaac  

(Automatically updated: 2025-10-21 23:42:15)

---

## 5. System Hierarchy

```text
crypto-futures-dashboard/
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ backend/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ src/
Ã¢â€â€š   Ã¢â€â€š   Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ futuresboard/
Ã¢â€â€š   Ã¢â€â€š       Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ app.py
Ã¢â€â€š   Ã¢â€â€š       Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ metrics.py
Ã¢â€â€š   Ã¢â€â€š       Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ db.py
Ã¢â€â€š   Ã¢â€â€š       Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ scraper.py
Ã¢â€â€š   Ã¢â€â€š       Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ futures.db
Ã¢â€â€š   Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ tests/
Ã¢â€â€š       Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ test_api.py
Ã¢â€â€š
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ frontend/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ src/
Ã¢â€â€š   Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ App.jsx
Ã¢â€â€š   Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ components/
Ã¢â€â€š   Ã¢â€â€š   Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ hooks/
Ã¢â€â€š   Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ public/
Ã¢â€â€š       Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ index.html
Ã¢â€â€š
Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ docs/
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ architecture.md
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ developer_guide.md
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ quant_blueprint_synced.md
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ continuity_state.md
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ continuity_log.json
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ status_report.md
    Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ status_report_template.md
```

This structure ensures all operational and documentation layers are version-tracked and recoverable.

---

## 6. PowerShell Continuity Framework

**Core Functions**

| Function | Purpose |
|-----------|----------|
| `Invoke-ContinuitySnapshot` | Captures backend state, uptime %, and file hashes |
| `Invoke-StatusReport` | Generates Markdown reports after snapshots |
| `Invoke-DevOpsAutoSync` | Pushes and backs up all docs to GitHub |
| `Sync-Continuity` | Runs full continuity workflow (snapshot Ã¢â€ â€™ commit Ã¢â€ â€™ push) |
| `Invoke-PhaseTrack` | Updates quant progress tracker and commits phase notes |

All functions are defined in `Microsoft.PowerShell_profile.ps1` and registered for automatic execution.

---

## 7. Future Additions

- Ã°Å¸Å¸Â¦ **Health Tiering:** Enhanced `/health` route with Ã¢â‚¬Å“degradedÃ¢â‚¬Â status.  
- Ã°Å¸Å¸Â§ **Alert Refinement:** Phase P3.7 introduces tier-2 confluence alerts.  
- Ã°Å¸Å¸Â© **Replay Engine:** Simulated backtest engine (Phase P4.0).  
- Ã°Å¸Å¸Â¨ **Frontend Sync:** Display `status_report.md` summaries in UI widgets.

---

_Last updated automatically by the Continuity Framework Ã¢â‚¬â€œ {{timestamp}}_
