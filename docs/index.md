# Crypto Futures Dashboard – Documentation Index

Welcome to the project documentation hub for the **Crypto Futures Dashboard**.

This repository documents both the live trading backend (`futuresboard`) and the
frontend analytics dashboard.  
Every document in `/docs` is maintained by the **Continuity Framework** through
automated snapshots, syncs, and phase tracking.

---

## 🔗 Quick Access

| Area | Description | File |
|------|--------------|------|
| **Architecture Overview** | System design, components, and data flow | [architecture.md](architecture.md) |
| **Developer Guide** | API reference, environment setup, and testing | [developer_guide.md](developer_guide.md) |
| **Quant Blueprint** | Quantitative model design and progress tracker | [quant_blueprint_synced.md](quant_blueprint_synced.md) |
| **Continuity Framework** | Snapshot and sync automation details | [continuity_diagram.md](continuity_diagram.md) |
| **Status Reports** | Auto-generated backend health summaries | [status_report.md](status_report.md) |

---

## 📘 How Docs Update

All `.md` files in this directory are updated by PowerShell profile functions:

| Function | Purpose |
|-----------|----------|
| `Invoke-ContinuitySnapshot` | Captures backend state and uptime |
| `Invoke-StatusReport` | Generates this phase’s system summary |
| `Invoke-PhaseTrack` | Updates quant progress tracker |
| `Sync-Continuity` | Runs full doc + backup + commit cycle |

Use `Sync-Continuity` to keep documentation aligned with the live project.

---

_Last updated automatically by the Continuity Framework._
