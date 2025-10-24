import asyncio
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))
from futuresboard import quant_engine

async def test_quant_engine():
    # compute 1 iteration of metrics (without infinite loop)
    computed = await quant_engine.compute_quant_metrics(limit=50)
    print(f"✅ computed {len(computed)} metrics")

    # persist those features
    saved = await quant_engine.persist_5s_features(computed)
    print(f"✅ persisted {saved} quant_features_5s rows")

    # diagnostics pass
    diags = await quant_engine.compute_quant_diagnostics(["BTCUSDT", "ETHUSDT"])
    print(f"✅ diagnostics computed for {len(diags)} symbols")

    # signals pass
    sigs = await quant_engine.compute_signal_families()
    print(f"✅ signal families generated: {len(sigs)}")

    # confluence
    confs = await quant_engine.compute_confluence_scores()
    print(f"✅ confluence scores: {len(confs)}")

    # regimes
    await quant_engine.regime_loop(interval=0.1)  # test one iteration manually? skip loop

asyncio.run(test_quant_engine())
