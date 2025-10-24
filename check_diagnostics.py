import asyncio
from backend.src.futuresboard import db

async def main():
    await db.init_db_async()
    conn = await db._pool.acquire()
    r = await conn.fetch("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='quant_diagnostics'")
    for row in r:
        print(row)
    await db._pool.release(conn)
    await db.close_db_async()

asyncio.run(main())