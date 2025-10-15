# Run: python seed_metrics.py (from backend/)
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))  # Fixes imports in new root
from futuresboard.metrics import get_all_metrics, save_metrics_to_db  # Your modded
metrics = asyncio.run(get_all_metrics('5m'))
save_metrics_to_db(metrics)
print(f"Seeded {len([m for m in metrics if m])} pairs")