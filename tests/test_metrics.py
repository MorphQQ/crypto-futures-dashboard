import asyncio
from backend.src.futuresboard.metrics import get_all_metrics  # Absolute from backend/tests

def test_weighted_oi():
    metrics = asyncio.run(get_all_metrics(limit=5))
    assert len(metrics) >= 4  # Top-vol jitter ; >=4 stable P3
    assert 'weighted_oi_usd' in metrics[0]
    assert metrics[0]['weighted_oi_usd'] > 0

if __name__ == "__main__":
    test_weighted_oi()