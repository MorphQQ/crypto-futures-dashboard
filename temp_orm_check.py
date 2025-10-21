from backend.src.futuresboard.db import engine, Metric
from sqlalchemy import inspect

insp = inspect(engine)
print("=== ORM Inspection ===")
print("Tables:", insp.get_table_names())

print("\nColumns for metrics:")
for c in insp.get_columns("metrics"):
    print(f" - {c['name']} ({c['type']})")
