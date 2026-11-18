"""Send many requests concurrently, with a limit on how many run at once."""
from __future__ import annotations

import asyncio

from .client import ClientSession


def _normalize(spec):
    """Accept a URL string, a (method, url[, kwargs]) tuple, or a dict with 'url'."""
    if isinstance(spec, str):
        return "GET", spec, {}
    if isinstance(spec, tuple):
        if len(spec) not in (2, 3):
            raise TypeError("tuple spec must be (method, url) or (method, url, kwargs)")
        method, url = spec[0], spec[1]
        kwargs = dict(spec[2]) if len(spec) > 2 else {}
        return method, url, kwargs
    if isinstance(spec, dict):
        if "url" not in spec:
            raise TypeError("dict spec must contain 'url'")
        spec = dict(spec)
        method = spec.pop("method", "GET")
        url = spec.pop("url")
        return method, url, spec
    raise TypeError(f"Unsupported request spec: {spec!r}")


def _check_concurrency(concurrency: int) -> None:
    # Semaphore(0) would block every request forever, so refuse it up front.
    if not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError("concurrency must be an integer >= 1")


async def gather_requests(
    session: ClientSession,
    requests,
    *,
    concurrency: int = 100,
    return_exceptions: bool = True,
) -> list:
    """Run many requests on one session, like asyncio.gather but bounded.

    Each item in `requests` can be:
      - a URL string               -> GET
      - (method, url)              -> e.g. ("GET", url)
      - (method, url, {kwargs})    -> kwargs go to session.request (json=, data=, headers=...)
      - {"method": ..., "url": ..., "json": ...}

    Results come back in the same order as the input. With return_exceptions=True
    (default), a failed request is returned as its exception object, so one
    failure never cancels the others.
    """
    _check_concurrency(concurrency)
    sem = asyncio.Semaphore(concurrency)

    async def run(spec):
        async with sem:
            method, url, kwargs = _normalize(spec)
            return await session.request(method, url, **kwargs)

    tasks = [run(spec) for spec in requests]
    return await asyncio.gather(*tasks, return_exceptions=return_exceptions)


async def fetch_all(
    requests,
    *,
    concurrency: int = 100,
    timeout: float = 10.0,
    return_exceptions: bool = True,
) -> list:
    """One-liner: open a session, run all requests, close the session.

        results = await fetch_all(["https://a.com", "https://b.com"])
    """
    _check_concurrency(concurrency)
    async with ClientSession(timeout=timeout, max_per_host=concurrency) as session:
        return await gather_requests(
            session,
            requests,
            concurrency=concurrency,
            return_exceptions=return_exceptions,
        )
