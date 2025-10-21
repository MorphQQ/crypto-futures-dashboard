from backend.src.futuresboard.db import get_latest_metrics

print("=== TEST get_latest_metrics(limit=3) ===")
rows = get_latest_metrics(limit=3)
print(f"Returned type: {type(rows)}")
print(f"Row count: {len(rows)}")
print("Sample (first row):", rows[0] if rows else None)
