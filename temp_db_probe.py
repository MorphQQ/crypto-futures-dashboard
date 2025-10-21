from backend.src.futuresboard.db import get_latest_metrics, get_metrics_by_symbol

print("=== get_latest_metrics() ===")
rows = get_latest_metrics(limit=3)
print("Returned:", type(rows), "count:", len(rows))
if rows:
    print("Sample row 0:", rows[0])

print("\n=== get_metrics_by_symbol('BTC') ===")
rows2 = get_metrics_by_symbol("BTC", limit=3)
print("Returned:", type(rows2), "count:", len(rows2))
if rows2:
    print("Sample row 0:", rows2[0])
