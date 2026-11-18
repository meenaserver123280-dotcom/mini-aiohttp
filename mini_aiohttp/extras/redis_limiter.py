"""Rate limiting shared across processes, backed by Redis.

No extra package is needed: pass any async Redis client you already use
(for example redis.asyncio.Redis). The limiter uses fixed one-second windows
and INCR/EXPIRE, so it works the same on every server instance.

    import redis.asyncio as redis
    from mini_aiohttp.extras.redis_limiter import RedisRateLimiter

    limiter = RedisRateLimiter(redis.Redis(), rate=20)   # 20 requests per second per client
    app.middleware_(limiter.middleware())
"""
from __future__ import annotations

import math
import time

from ..response import Response


class RedisRateLimiter:
    def __init__(self, client, *, rate: int, window: int = 1, prefix: str = "mini-aiohttp:rl",
                 clock=time.time):
        if rate < 1 or window < 1:
            raise ValueError("rate and window must be >= 1")
        self.client = client
        self.rate = rate
        self.window = window
        self.prefix = prefix
        self._clock = clock

    async def check(self, key: str):
        """Return None if the request is allowed, else seconds until the window resets."""
        now = self._clock()
        bucket = int(now // self.window)
        redis_key = f"{self.prefix}:{key}:{bucket}"
        count = await self.client.incr(redis_key)
        if count == 1:
            await self.client.expire(redis_key, self.window + 1)
        if count <= self.rate:
            return None
        return max(1.0, (bucket + 1) * self.window - now)

    def middleware(self):
        async def middleware(req, call_next):
            wait = await self.check(req.client or "unknown")
            if wait is not None:
                return Response(429, "Too Many Requests", {"Retry-After": str(math.ceil(wait))})
            return await call_next(req)

        return middleware
