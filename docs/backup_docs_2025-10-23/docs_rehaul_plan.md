# 📘 Documentation Rehaul Plan v1.0 – Oct 2025

**Project:** Crypto Futures Dashboard  
**Author:** Lian Isaac  
**Framework:** PowerShell Continuity v1.3.8  
**Generated:** 2025-10-21  

---

## I. Purpose

This document defines the structure, automation hooks, and maintenance workflow for all documentation under `/docs`. It aligns with the Continuity Framework (PowerShell-based) that governs automated state tracking, phase commits, and backend diagnostics for the **Crypto Futures Dashboard**.

Goal: to maintain a clear, minimal, and self-updating documentation system that mirrors the project’s real backend state.

---

## II. Docs Directory Structure (Target)

| File | Description | Source | Status |
|------|--------------|---------|--------|
| **README.md** | Entry index and navigation for all docs. | Manual | ✅ Stable |
| **continuity_state.md** | Human-readable backend health + uptime summary. | Auto (`Invoke-ContinuitySnapshot`) | 🟢 Generated |
| **continuity_log.json** | Historical hashes + backend health entries. | Auto (`Invoke-ContinuitySnapshot`) | 🟢 Generated |
| **project_context_v3.json** | Unified backend + repo + system export. | Auto (`Invoke-ContinuitySnapshot`) | 🟢 Generated |
| **quant_progress_tracker.md** | Quant KPIs and checklist tracker. | Auto (`Invoke-PhaseTrack` → `gen_tracker.py`) | 🟢 Generated |
| **quant_blueprint.md** | Quant architecture, metrics, and formulas. | Manual (updated via `gen_blueprint.py`) | ⚪ Semi-Static |
| **api_guide.md** | REST + WebSocket endpoint reference. | Manual | ⚪ Static |
| **roadmap.md** | Execution plan (architecture, UX, ops). | Manual | ⚪ Static |
| **testing_harness.md** | Pytest suite + quant validation methods. | Manual | ⚪ Static |
| **status_report.md** | Generated snapshot summary for continuity reports. | Auto (via GPT or `Invoke-ContinuitySnapshot`) | 🔜 Planned |

---

## III. Automation Hooks Overview

| PowerShell Function | Primary Role | Key Artifacts |
|----------------------|---------------|----------------|
| **`Invoke-PhaseTrack`** | Commits phase updates and appends new KPIs to tracker. | `quant_progress_tracker.md` |
| **`Invoke-RepoIngest`** | Generates repo trees, summaries, or file teasers for chat. | Clipboard / console output |
| **`Invoke-ContinuitySnapshot`** | Captures backend uptime, hashes, and exports Markdown + JSON docs. | `continuity_state.md`, `project_context_v3.json` |
| **`Sync-Continuity`** | Full pipeline: snapshot → commit → compare → summarize → update context. | All docs auto-refreshed |
| **`Invoke-DevOpsAutoSync`** | Safe Git commit + backup with exponential retry. | `/backup_docs_YYYY-MM-DD` |
| **`Show-ContinuityStatus`** | Console one-line summary of backend state. | Terminal only |
| **`Show-ContinuitySummary`** | 24h uptime breakdown from log history. | Console only |
| **`Compare-ContinuityHashes`** | Diffs SHA256 hashes across snapshots. | Terminal only |

---

## IV. Documentation Categories

### 🧩 Auto-Generated (Dynamic)
- `continuity_state.md`
- `continuity_log.json`
- `project_context_v3.json`
- `quant_progress_tracker.md`

These are updated automatically via PowerShell functions during `Sync-Continuity` or phase transitions. **Do not edit manually.**

### 🧱 Manual (Static)
- `README.md`
- `roadmap.md`
- `api_guide.md`
- `quant_blueprint.md`
- `testing_harness.md`

These documents represent conceptual and reference materials. Edits should be followed by a phase commit:
```powershell
Invoke-PhaseTrack -p 3 -m "Docs revision (blueprint/roadmap update)"
```

### 🧾 Semi-Auto (Hybrid)
- `status_report.md` *(new planned)* — generated post-sync using GPT or manual Markdown export.

---

## V. Maintenance Workflow

### 1️⃣ Daily Auto Snapshot
- Scheduled via Task Scheduler (`CryptoFutures_Continuity_AutoSnapshot`).
- Runs every 6 hours → updates `continuity_state.md` and `project_context_v3.json`.

### 2️⃣ Manual Phase Commit
```powershell
Invoke-PhaseTrack -p 3 -m "Weighted OI + Alerts update"
```
This triggers KPI regeneration (`gen_tracker.py`) and commits with a phase label.

### 3️⃣ Full Sync + Backup
```powershell
Sync-Continuity -Phase "P3.6 - UTF8 Logging + QuantSummary Stable" -Note "Routine sync"
```
This sequence:
- Captures backend health
- Updates JSON + Markdown states
- Commits + backs up `/docs`

### 4️⃣ Recovery + Health Checks
```powershell
Test-ProfileHealth
Show-ContinuitySummary
```
Used for confirming task integrity and uptime calculations.

---

## VI. Future Documentation Extensions

| Target | Description | Integration |
|---------|--------------|--------------|
| **`status_report.md`** | AI-generated snapshot summary (phase, uptime, quant KPIs). | Post-sync hook (GPT integration) |
| **`phase_summary.md`** | Detailed backend + quant report per phase transition. | `Invoke-PhaseTrack` extension |
| **GitHub Mirror Sync** | Auto-publish docs folder to GitHub Pages. | PowerShell GitHub Action mirror |
| **Blueprint Auto-KPI Merge** | Merge KPIs from DB logs into `quant_blueprint.md`. | Python + PowerShell hybrid |

---

## VII. Conventions

| Category | Convention |
|-----------|-------------|
| **Versioning** | `vX.Y` in top metadata; updated manually for static docs. |
| **Timestamps** | ISO `yyyy-MM-dd HH:mm:ss` (local). |
| **Encoding** | UTF-8 for all Markdown + JSON. |
| **Commit Style** | `Phase P#: <change summary>` or `Docs: <section>` |
| **Backup Retention** | 7 days of `/backup_docs_YYYY-MM-DD` folders. |

---

## VIII. Summary

This plan establishes a unified, automated documentation workflow for the Crypto Futures Dashboard. All operational state data flows through PowerShell continuity commands and auto-syncs into Markdown/JSON artifacts, while conceptual files remain human-curated.

> Next Step → Generate `status_report.md` after the next successful `Sync-Continuity` to complete the new documentation ecosystem.

---

**End of Document**  
Version: v1.0 – October 2025  
Maintainer: Lian Isaac