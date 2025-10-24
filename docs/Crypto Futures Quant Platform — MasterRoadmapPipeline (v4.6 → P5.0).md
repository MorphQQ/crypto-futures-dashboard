📘 Crypto Futures Quant Platform — Master Backend Roadmap (v4.8 Comprehensive)

Owner: Lian Isaac
Phase: P4.4 — Full Backend Continuity & Config Integration Audit
System: Real-Time Crypto Futures Quant Research & Scalping Platform
Backend Stack: Quart + Hypercorn (ASGI) / PostgreSQL (Timescale-ready) / AsyncIO / SocketIO
Frontend Stack (Future-Aware): Vite + Tremor + Recharts
Automation: PowerShell Continuity Framework
Runtime Environment: Python 3.12+ / AsyncPG / Windows (PowerShell v1.4.6)

🧩 Phase Overview (v4.8)

This phase establishes a stable, fully-audited backend foundation ready for the upcoming dynamic settings and API expansion layers.
The emphasis is on async hygiene, database safety, config unification, and lifecycle continuity.

Objectives:

Audit and standardize all backend modules (core + quant).

Unify configuration (Pydantic + .env overlay) with reload support.

Fix legacy async/pool misuse and cancellation handling.

Ensure safe schema, consistent inserts, and proper Timescale compatibility.

Establish runtime reload (/api/system/reload-config) baseline for P5.0 Dynamic Settings.

⚙️ Audit Scope & Deliverables
Layer	File	Purpose	Status
Core App	backend/src/futuresboard/app.py	Startup, ASGI init, background loops, routes	⏳ Next
Database	backend/src/futuresboard/db.py	Schema, inserts, metrics persistence	✅ Complete
Quant Engine	backend/src/futuresboard/quant_engine.py	Core computation, feature generation	⏳ Pending audit
WebSocket Manager	backend/src/futuresboard/ws_manager.py	Stream orchestration, reconnection	⏳ Pending audit
REST Collector	backend/src/futuresboard/rest_collector.py	Binance REST ingestion and storage	⏳ Pending audit
Config	backend/src/futuresboard/config.py	Unified settings + reload logic	✅ Complete
Utility	backend/src/futuresboard/utils.py	Float safety, JSON sanitation, helper tools	⏳ Needed
Quant Modules	quant_diagnostics.py, quant_signals.py, quant_confluence.py	Loop separation, data derivations	⏳ Optional audit
System API	/api/system/reload-config	Runtime reload of .env/config	✅ Implemented
🧠 Audit Goals
A. Structural & Async Hygiene

Replace any await db._pool.release(conn) with context-managed async with db._pool.acquire().

Add try/except asyncio.CancelledError around all loops for graceful cancellation.

Wrap WS session cleanup (_manager_session.close()) in try/finally.

Drain queues with task_done() after each item to avoid blocking.

Validate all background tasks (quant_loop, diagnostics_loop, etc.) cancel cleanly on shutdown.

B. Schema & Persistence

Remove duplicate quant_regimes creation in init_db_async.

Remove duplicate save_quant_context_scores_async (keep robust version with sanitize_json).

Fix quant_summary index to:

CREATE UNIQUE INDEX IF NOT EXISTS quant_summary_symbol_tf_idx
  ON quant_summary(symbol, timeframe);


Add Timescale-safe compatibility:

CREATE EXTENSION IF NOT EXISTS timescaledb;


Adjust prune loop to sleep outside transaction block.

C. Performance Optimizations

Use asyncio.gather for REST collector multi-endpoint fetches.

Move CPU-bound metrics (NumPy, correlations) to asyncio.to_thread() in quant_engine.

Evaluate asyncpg COPY for backfills (Phase P5+).

Log execution latency per loop for diagnostics.

D. Config & Environment Unification

All configuration now flows through:

from .config import get_settings
cfg = get_settings()


.env defines DATABASE_URL, SYMBOLS, intervals, log level, and phase.

Config supports hot-reload:

from functools import lru_cache
def reload_settings():
    get_settings.cache_clear()
    return get_settings()


/api/system/reload-config endpoint reinitializes settings live.

E. Safety & Monitoring

Verify retry/backoff logic for REST/WS tasks.

Add watchdog or TaskGroup (Python 3.11+) for WS and REST health.

Extend logs with cfg.LOG_LEVEL globally:

logging.basicConfig(level=getattr(logging, cfg.LOG_LEVEL))

🧩 File-by-File Backend Audit Plan
Order	File	Focus
1️⃣	app.py	Startup lifecycle, async task creation, shutdown sequence
2️⃣	db.py	(Done ✅) — Confirm schema, pool handling, inserts
3️⃣	quant_engine.py	Loops, metrics computation, cancellation & pool safety
4️⃣	ws_manager.py	WebSocket management, subscription lifecycle
5️⃣	rest_collector.py	REST polling, batching, data persistence
6️⃣	utils.py	Safe float / JSON handling utilities
7️⃣	quant modules	Cross-loop validation & feature synchronization
📊 Deliverables

backend_continuity_report.md — audit findings, changes, and test status

continuity_state.json — PowerShell continuity record update

config/default_settings.json — base for Phase 5 Dynamic Settings

api/system/reload-config — config reload endpoint (✅ done)

settings_manager.py — next phase scaffold

🔁 Phase Transitions
From	To	Action
P4.3	P4.4	Begin full backend audit and config integration
P4.4	P4.5	Add persistent settings and live reload APIs
P4.5	P4.6	Expand dashboard-ready API endpoints
P4.6	P5.0	Integrate Tremor/Recharts frontend panels
🧭 Operational Command Map (Continuity Framework)
Purpose	Command	Notes
Validate PS profile	Test-ProfileHealth	Checks Git + continuity functions
Ingest repo structure	Invoke-RepoIngest -mode tree -depth 3	Prints filtered file tree
Ingest specific file	Invoke-RepoIngest -mode chat -filePath 'backend/src/futuresboard/app.py'	Paste content for audit
Capture snapshot	Invoke-ContinuitySnapshot -Phase 'P4.4 - Backend Continuity & Config Integration Audit'	Saves uptime, file hashes
Sync changes	Sync-Continuity -Phase 'P4.4 - Backend Optimization Complete'	Push to Git + docs
Generate report	Invoke-StatusReport	Summarizes backend audit
Update docs	Invoke-AutoDocsUpdate	Refreshes architecture.md
Push safely	Invoke-DevOpsAutoSync -Silent	Commit & backup docs
Compare hashes	Compare-ContinuityHashes -idxA 0 -idxB 1	Detects drift
Start next phase	Invoke-PhaseTrack -p 5 -m 'Dynamic Settings Layer Init'	Move to P5.0
✅ Next Step

We begin the file-by-file backend audit sequence.

Starting file:
backend/src/futuresboard/app.py

We’ll:

Re-ingest it fully (Invoke-RepoIngest -mode chat -filePath 'backend/src/futuresboard/app.py')

Verify async/task structure, logging, and startup/shutdown

Propose corrections inline with ✅ or ⚠️ marks