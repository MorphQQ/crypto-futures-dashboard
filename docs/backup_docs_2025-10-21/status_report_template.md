# Project Status Report Template - Crypto Futures Dashboard

**Generated:** {{timestamp}}  
**Phase:** {{phase}}  
**Backend:** {{backend_name}}  
**Maintainer:** Lian Isaac  

---

## Overview

| Field | Value |
|-------|-------|
| **Phase** | {{phase}} |
| **Backend Health** | {{backend_health}} |
| **Uptime (7-Sample)** | {{uptime_pct}} % |
| **System** | {{system_info}} |
| **Database Path** | {{db_path}} |

---

## Backend Summary

**Last Snapshot:** {{timestamp}}  
**Health Check:** {{health_message}}  

### Key Components
| File | Hash | Last Updated |
|------|------|---------------|
| metrics.py | {{hash_metrics}} | {{timestamp}} |
| db.py | {{hash_db}} | {{timestamp}} |
| app.py | {{hash_app}} | {{timestamp}} |
| scraper.py | {{hash_scraper}} | {{timestamp}} |
| App.jsx | {{hash_appjsx}} | {{timestamp}} |

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

**Python:** {{python_version}}  
**Node:** {{node_version}}  
**OS:** {{os_version}}  
**User:** {{user}}  

**Repo Summary:**  
```text
{{repo_summary}}
```

---

## Diagnostic Summary

- **Backend Status:** {{backend_health}}  
- **Probable Cause (if unhealthy):** {{diagnostic_hint}}  
- **Last Known Healthy Snapshot:** {{last_healthy_timestamp}}  

---

## Recommendations

1. Verify `/health` endpoint response.  
2. Check `backend/logs/app.log` for UTF-8 encoding errors.  
3. Re-run `Invoke-ContinuitySnapshot` after confirming backend fix.  
4. Commit with `Invoke-PhaseTrack -p {{next_phase}} -m "Docs + Stability Update"`.  

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
