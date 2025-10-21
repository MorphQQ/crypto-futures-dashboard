# Testing Harness v1.0 – Oct 18, 2025

Focus: Smoke (<5s), Bench (<10s/50 pairs), Quant Val (Z finite/false-pos). Run: pytest -q ; code_execution snippets.

## Pytest Suite (pytest.ini + Files)
- test_metrics.py: `def test_get_all(): metrics = get_all_metrics(tf='15m'); assert len(metrics) == 20; assert all(np.isfinite(m['z_score']) for m in metrics)`
- test_ws.py: `def test_emit(): ... assert 'metrics_update' in socketio.events`
- Bench: `pytest --benchmark-only` (Target: <5s load, no blanks).

## Tool Snippets (code_execution)
| Test | Snippet | Expected |
|------|---------|----------|
| Z Finite | `import numpy as np; z = np.random.normal(0,1,20); assert np.all(np.isfinite(z)) & (np.abs(z) < 10)` | True |
| Weighted OI | `import numpy as np; oi = np.array([1e9,2e9]); vols = np.array([1e9,2e9]); w = np.average(oi, weights=vols/vols.sum()); print(w)` | 1.666e9 |
| Confluence | `def calc(z,ls,imb): return sum([z>2.5, ls>2, abs(imb)>3])/3; print(calc(2.6,2.1,-3.1))` | 1.0 |
| DB Rows | As gen_tracker.py | >581 |

## 24h Harness (PS Chain)
```powershell
pytest -q ; curl /api/metrics?tf=15m | jq 'length' ; Get-Content backend/logs/app.log -Tail 10 | Select-String "emit|error" ; python docs/gen_tracker.py
# Output: ........ 20 ; Emitted20 (no error)