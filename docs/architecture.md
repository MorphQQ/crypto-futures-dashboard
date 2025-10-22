# Architecture Overview â€“ Crypto Futures Dashboard

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
     â”‚
     â”œâ”€â”€ continuity_state.json
     â”‚
     â”œâ”€â”€ project_context_v3.json
     â”‚
     â”œâ”€â”€ Invoke-StatusReport
     â”‚
     â””â”€â”€ status_report.md â†’ Safe-GitPush â†’ GitHub
```

Each layer reflects the projectâ€™s **live backend status** and **phase tag**.

---

## 4. Current Phase

**Phase:** P3.6 - Weighted OI Fix
**Backend:** futuresboard
**Maintainer:** Lian Isaac  

(Automatically updated: 2025-10-22 14:20:45)

---

## 5. System Hierarchy

```text
crypto-futures-dashboard/
â”œâ”€â”€ backend/
â”‚   â”œâ”€â”€ src/
â”‚   â”‚   â””â”€â”€ futuresboard/
â”‚   â”‚       â”œâ”€â”€ app.py
â”‚   â”‚       â”œâ”€â”€ metrics.py
â”‚   â”‚       â”œâ”€â”€ db.py
â”‚   â”‚       â”œâ”€â”€ scraper.py
â”‚   â”‚       â””â”€â”€ futures.db
â”‚   â””â”€â”€ tests/
â”‚       â””â”€â”€ test_api.py
â”‚
â”œâ”€â”€ frontend/
â”‚   â”œâ”€â”€ src/
â”‚   â”‚   â”œâ”€â”€ App.jsx
â”‚   â”‚   â”œâ”€â”€ components/
â”‚   â”‚   â””â”€â”€ hooks/
â”‚   â””â”€â”€ public/
â”‚       â””â”€â”€ index.html
â”‚
â””â”€â”€ docs/
    â”œâ”€â”€ architecture.md
    â”œâ”€â”€ developer_guide.md
    â”œâ”€â”€ quant_blueprint_synced.md
    â”œâ”€â”€ continuity_state.md
    â”œâ”€â”€ continuity_log.json
    â”œâ”€â”€ status_report.md
    â””â”€â”€ status_report_template.md
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
| `Sync-Continuity` | Runs full continuity workflow (snapshot â†’ commit â†’ push) |
| `Invoke-PhaseTrack` | Updates quant progress tracker and commits phase notes |

All functions are defined in `Microsoft.PowerShell_profile.ps1` and registered for automatic execution.

---

## 7. Future Additions

- ðŸŸ¦ **Health Tiering:** Enhanced `/health` route with â€œdegradedâ€ status.  
- ðŸŸ§ **Alert Refinement:** Phase P3.7 introduces tier-2 confluence alerts.  
- ðŸŸ© **Replay Engine:** Simulated backtest engine (Phase P4.0).  
- ðŸŸ¨ **Frontend Sync:** Display `status_report.md` summaries in UI widgets.

---

_Last updated automatically by the Continuity Framework â€“ {{timestamp}}_
inuity Data Flow

