import asyncio
import json
import aiohttp
from typing import List

BINANCE_WS_BASE = "wss://fstream.binance.com/stream?streams="  # For USDS-M futures

async def connect_and_listen(session: aiohttp.ClientSession, url: str, handle_msg):
    try:
        async with session.ws_connect(url, heartbeat=150) as ws:  # Adjusted heartbeat
            print("Connected to", url)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await handle_msg(data)
                elif msg.type == aiohttp.WSMsgType.PING:
                    await ws.pong()
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print("WS Error:", msg)
                    break
    except aiohttp.ClientError as e:
        print("Connection error:", e)
        await asyncio.sleep(5)  # Simple backoff alternative

async def build_combined_stream(pair_streams: List[str]) -> str:
    return BINANCE_WS_BASE + "/".join(pair_streams)

async def handle_message(data):
    # Example: Extract markPrice, OI if subscribed
    stream = data["stream"]
    if "markPrice" in stream:
        print("Mark Price Update:", data["data"]["p"])  # Price
    # Push to queue/DB here

async def start_stream_worker(pairs: List[str]):
    streams = [f"{p.lower()}@markPrice@1s" for p in pairs]  # 1s updates
    streams += [f"{p.lower()}@openInterest@1h" for p in pairs]  # OI hourly
    url = await build_combined_stream(streams)
    async with aiohttp.ClientSession() as session:
        while True:  # Reconnect loop
            await connect_and_listen(session, url, handle_message)
            await asyncio.sleep(5)  # Backoff

# Example run (in futuresboard: call in thread)
if __name__ == "__main__":
    pairs = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    asyncio.run(start_stream_worker(pairs))