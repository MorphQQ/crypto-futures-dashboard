# API Guide v1.0 – Oct 18, 2025

Base: http://127.0.0.1:5000 | Auth: None (Read-Only) | Format: JSON (Content-Range pag).

## Endpoints

| Route | Method | Params | Desc | Example Curl | Response Tease |
|-------|--------|--------|------|--------------|----------------|
| /api/metrics | GET | ?tf=15m&limit=20 | Paginated quants (OI/LSΔ/Z etc.) | `curl "http://127.0.0.1:5000/api/metrics?tf=15m" | jq '.[] | {sym, z_score}'` | 200 {data: [{sym:"ETH", z:1.20, confluence:0.33}], range:0-19/20} |
| /health | GET | None | Uptime/rate/errors | `curl http://127.0.0.1:5000/health` | 200 {"uptime":99.9, "req_rate":350, "errors":0} |
| /api/replay (P4) | GET | ?tf=1h&start_ts=... | Bisect ts joins sim | `curl "/api/replay?tf=1h" | jq '.events[]'` | Tease: {events: [{ts, z_spike:true}]} |
| WS /socket.io | WS | Emit: metrics_update | Batch tf quants + alerts | Frontend: socket.io-client connect | On: 'alert_toast' {sym:"BTC", type:"Z_spike", val:2.6} |

## Validation (code_execution Tease)
```python
import requests
r = requests.get('http://127.0.0.1:5000/api/metrics?tf=15m')
data = r.json()
print(len(data['data']), 'pairs; Z finite:', all(abs(d['z_score']) < 10 for d in data['data']))
# Output: 20 pairs; Z finite: True