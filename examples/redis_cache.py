"""Redis as a cache in front of a slow handler.

    pip install "mini-aiohttp[redis]"
    REDIS_URL=redis://localhost:6379/0 python examples/redis_cache.py
"""
import asyncio
import os

from mini_aiohttp import Response, Server, serve
from mini_aiohttp.db import RedisClient

cache = RedisClient(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
server = Server()


@server.route("/price/{symbol}")
async def price(req):
    symbol = req.params["symbol"].upper()
    key = f"price:{symbol}"
    cached = await cache.client.get(key)
    if cached is not None:
        return Response(200, cached.decode("utf-8"))
    value = "42.00"  # replace with a slow lookup
    await cache.client.set(key, value, ex=60)  # expire after 60 seconds
    return Response(200, value)


async def main():
    await cache.open()
    await serve(server, "127.0.0.1", 8080, shutdown=[cache.close])


if __name__ == "__main__":
    asyncio.run(main())
