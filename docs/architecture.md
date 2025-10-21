# Architecture Overview – Crypto Futures Dashboard

## 1. System Summary

**Backend:** Flask + SocketIO application (`futuresboard`)  
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
     │
     ├── continuity_state.json
     │
     ├── project_context_v3.json
     │
     ├── Invoke-StatusReport
     │
     └── status_report.md → Safe-GitPush → GitHub
```

Each layer reflects the project’s **live backend status** and **phase tag**.

---

## 4. Current Phase

**Phase:** P3.6 – UTF8 Logging + QuantSummary Stable  
**Backend:** futuresboard  
**Maintainer:** Lian Isaac  

(Automatically updated during each `Sync-Continuity` run.)

---

## 5. System Hierarchy

```text
crypto-futures-dashboard/
├── backend/
│   ├── src/
│   │   └── futuresboard/
│   │       ├── app.py
│   │       ├── metrics.py
│   │       ├── db.py
│   │       ├── scraper.py
│   │       └── futures.db
│   └── tests/
│       └── test_api.py
│
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   └── hooks/
│   └── public/
│       └── index.html
│
└── docs/
    ├── architecture.md
    ├── developer_guide.md
    ├── quant_blueprint_synced.md
    ├── continuity_state.md
    ├── continuity_log.json
    ├── status_report.md
    └── status_report_template.md
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
| `Sync-Continuity` | Runs full continuity workflow (snapshot → commit → push) |
| `Invoke-PhaseTrack` | Updates quant progress tracker and commits phase notes |

All functions are defined in `Microsoft.PowerShell_profile.ps1` and registered for automatic execution.

---

## 7. Future Additions

- 🟦 **Health Tiering:** Enhanced `/health` route with “degraded” status.  
- 🟧 **Alert Refinement:** Phase P3.7 introduces tier-2 confluence alerts.  
- 🟩 **Replay Engine:** Simulated backtest engine (Phase P4.0).  
- 🟨 **Frontend Sync:** Display `status_report.md` summaries in UI widgets.

---

_Last updated automatically by the Continuity Framework – {{timestamp}}_
