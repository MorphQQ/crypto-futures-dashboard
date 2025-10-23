import asyncio
from backend.src.futuresboard import db

async def main():
    await db.init_db_async()
    conn = await db._pool.acquire()
    r = await conn.fetch('select count(*), min(ts), max(ts) from quant_features_5s')
    print(r)
    await db._pool.release(conn)
    await db.close_db_async()

asyncio.run(main())
