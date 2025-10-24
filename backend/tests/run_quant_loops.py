import asyncio
import sys, os

# 🧩 Windows compatibility fix for asyncio + asyncpg
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# add backend/src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from futuresboard import db, quant_engine


async def main():
    # 1️⃣ Ensure DB connection before running loops
    print("[startup] ensuring DB connection ...")
    async with db.DBConnection() as conn:
        await conn.execute("SELECT 1")
        print("[startup] ✅ Database ready")

    # 2️⃣ Launch all quant loops concurrently
    loops = [
        asyncio.create_task(quant_engine.run_quant_loop(interval=5.0)),
        asyncio.create_task(quant_engine.diagnostics_loop(interval=10)),
        asyncio.create_task(quant_engine.signals_loop(interval=10)),
        asyncio.create_task(quant_engine.confluence_loop(interval=10)),
        asyncio.create_task(quant_engine.regime_loop(interval=15)),
        asyncio.create_task(quant_engine.context_scoring_loop(interval_s=15)),
        asyncio.create_task(quant_engine.context_trends_loop(interval_s=15)),
    ]

    print("[main] quant loops started — running for 60s ...")

    try:
        await asyncio.sleep(60)  # run 1 minute
    except asyncio.CancelledError:
        pass
    finally:
        print("[shutdown] cancelling all loops ...")
        for t in loops:
            t.cancel()
        await asyncio.gather(*loops, return_exceptions=True)
        await db.close_db_async()
        print("✅ All quant loops ran and shutdown cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
