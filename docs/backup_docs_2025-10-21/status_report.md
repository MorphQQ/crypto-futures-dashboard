# Project Status Report Template - Crypto Futures Dashboard

**Generated:** 2025-10-21 23:46:44  
**Phase:** P3.6 - UTF8 Logging + QuantSummary Stable  
**Backend:** futuresboard  
**Maintainer:** Lian Isaac  

---

## Overview

| Field | Value |
|-------|-------|
| **Phase** | P3.6 - UTF8 Logging + QuantSummary Stable |
| **Backend Health** | unhealthy |
| **Uptime (7-Sample)** | 40.0 % |
| **System** | Microsoft Windows 10 Pro |
| **Database Path** | backend/src/futuresboard/futures.db |

---

## Backend Summary

**Last Snapshot:** 2025-10-21 23:46:44  
**Health Check:** unhealthy  

### Key Components
| File | Hash | Last Updated |
|------|------|---------------|
| metrics.py | {{hash_metrics}} | 2025-10-21 23:46:44 |
| db.py | {{hash_db}} | 2025-10-21 23:46:44 |
| app.py | {{hash_app}} | 2025-10-21 23:46:44 |
| scraper.py | {{hash_scraper}} | 2025-10-21 23:46:44 |
| App.jsx | {{hash_appjsx}} | 2025-10-21 23:46:44 |

---

## Quant Progress Snapshot

| Phase | % Complete | Status | Notes |
|--------|-------------|--------|--------|
| 1.5 Scaffold | {{phase_15}} | Done | Seed + WS Stable |
| 2 Core | {{phase_2}} | Done | TF Switch + Exports |
| 3 Adv | {{phase_3}} | In Progress | Weighted OI / Confluence |
| 4 Grand | {{phase_4}} | Planned | Replay Sim Planned |

### Quant KPIs
| KPI | Current | Target | Trend |
|------|----------|---------|--------|
| DB Rows | {{db_rows}} | >500 | {{db_trend}} |
| Avg Z-Score | {{avg_z}} | <2.0 | {{z_trend}} |
| Alert Accuracy | {{alert_acc}} | >95% | {{alert_trend}} |
| Confluence Hits | {{confluence_hits}} | >0.66 | {{confluence_trend}} |
| WS Uptime | {{ws_uptime}} | 99.9% | {{ws_trend}} |

---

## System Context

**Python:** Python 3.13.1  
**Node:** v22.20.0  
**OS:** Microsoft Windows 10 Pro  
**User:** Lian  

**Repo Summary:**  
```text

```

---

## Diagnostic Summary

- **Backend Status:** unhealthy  
- **Probable Cause (if unhealthy):** Check /health route and backend logs  
- **Last Known Healthy Snapshot:** 2025-10-21 23:46:51  

---

## Recommendations

1. Verify `/health` endpoint response.  
2. Check `backend/logs/app.log` for UTF-8 encoding errors.  
3. Re-run `Invoke-ContinuitySnapshot` after confirming backend fix.  
4. Commit with `Invoke-PhaseTrack -p P3.7 -m "Docs + Stability Update"`.  

---

## Next Steps

| Goal | Target Phase | ETA |
|------|----------------|-----|
| Weighted OI (Bybit merge) | P3.7 | {{eta_weighted}} |
| Tier2 Alerts (Confluence>0.66) | P3.7 | {{eta_alerts}} |
| Replay Engine Activation | P4.0 | {{eta_replay}} |

---

**End of Status Report Template**  
*To be populated automatically after each successful `Sync-Continuity` run.*

---
Backend Healthy - 2025-10-21 23:46:51