"""Retry policy for transient failures: 429 and 502/503/504 with jittered backoff.

Use it around ClientSession.request:

    policy = RetryPolicy(max_retries=3, base_delay=0.25)
    response = await policy.send(session, "GET", url)

Rules:
- 429 is retried for any method, because the server rejected the request before
  processing it. 502, 503 and 504 are retried only for idempotent methods.
- A numeric Retry-After header is honoured (capped at `retry_after_cap`). Otherwise the
  delay is exponential backoff with full jitter, capped at `max_delay`.
- Connection errors and timeouts are retried only for idempotent methods.
- Request bodies that are async iterables cannot be replayed, so they are never retried.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

from .client import ClientError

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"})


def _retry_after_seconds(value, cap: float):
    """Seconds from a Retry-After header, or None if it is missing or not a number."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None  # HTTP-date form: fall back to backoff
    if seconds < 0:
        return None
    return min(seconds, cap)


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 0.25
    max_delay: float = 10.0
    retry_statuses: tuple = (429, 502, 503, 504)
    retry_methods: frozenset = IDEMPOTENT_METHODS
    respect_retry_after: bool = True
    retry_after_cap: float = 60.0
    retry_errors: tuple = (ClientError, OSError, asyncio.TimeoutError)
    jitter: bool = True
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be 0 or more")
        if self.base_delay < 0 or self.max_delay < self.base_delay:
            raise ValueError("need 0 <= base_delay <= max_delay")
        self.retry_methods = frozenset(m.upper() for m in self.retry_methods)

    def delay(self, attempt: int, retry_after=None) -> float:
        """Seconds to wait before retry number `attempt + 1` (attempt counts from 0)."""
        if self.respect_retry_after:
            from_header = _retry_after_seconds(retry_after, self.retry_after_cap)
            if from_header is not None:
                return from_header
        ceiling = min(self.max_delay, self.base_delay * (2 ** attempt))
        if self.jitter:
            return self.rng.uniform(0, ceiling)
        return ceiling

    def should_retry_status(self, method: str, status: int) -> bool:
        if status == 429:
            return True
        return status in self.retry_statuses and method in self.retry_methods

    async def send(self, session, method: str, url: str, **kwargs):
        """Send one request through `session`, retrying transient failures.

        Returns the last response when retries run out. Raises the last error when
        a connection error is not retried any more.
        """
        method = method.upper()
        replayable = not hasattr(kwargs.get("data"), "__aiter__")
        attempt = 0
        while True:
            can_retry = attempt < self.max_retries and replayable
            try:
                response = await session.request(method, url, **kwargs)
            except self.retry_errors:
                if not can_retry or method not in self.retry_methods:
                    raise
                await asyncio.sleep(self.delay(attempt))
                attempt += 1
                continue
            if not can_retry or not self.should_retry_status(method, response.status):
                return response
            retry_after = response.headers.get("retry-after")
            await response.aclose()  # release the connection before waiting
            await asyncio.sleep(self.delay(attempt, retry_after))
            attempt += 1
