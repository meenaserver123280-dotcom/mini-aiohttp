"""Prometheus metrics for mini-aiohttp servers.

Requires: pip install "mini-aiohttp[prometheus]"

    from mini_aiohttp.extras.metrics import prometheus_middleware, metrics_handler

    app.middleware_(prometheus_middleware())
    app.route("/metrics")(metrics_handler())
"""
from __future__ import annotations

import time

from ..response import Response

# Prometheus refuses to register the same metric name twice in one registry, so the
# metric objects are created once per (registry, namespace) and reused.
_metric_cache: dict = {}


def _prom():
    try:
        import prometheus_client
    except ImportError as exc:
        raise RuntimeError('metrics need: pip install "mini-aiohttp[prometheus]"') from exc
    return prometheus_client


def prometheus_middleware(registry=None, *, namespace: str = "http"):
    """Count requests and record latency per method and status code."""
    prom = _prom()
    reg = registry if registry is not None else prom.REGISTRY
    key = (id(reg), namespace)
    if key not in _metric_cache:
        requests_total = prom.Counter(
            f"{namespace}_requests_total", "HTTP requests", ["method", "status"], registry=reg
        )
        latency = prom.Histogram(
            f"{namespace}_request_duration_seconds", "HTTP request latency in seconds",
            ["method"], registry=reg,
        )
        _metric_cache[key] = (requests_total, latency)
    requests_total, latency = _metric_cache[key]

    async def middleware(req, call_next):
        started = time.perf_counter()
        resp = await call_next(req)
        latency.labels(method=req.method).observe(time.perf_counter() - started)
        requests_total.labels(method=req.method, status=str(resp.status)).inc()
        return resp

    return middleware


def metrics_handler(registry=None):
    """Handler that returns the current metrics in Prometheus text format."""
    prom = _prom()
    reg = registry if registry is not None else prom.REGISTRY

    async def handler(req):
        return Response(200, prom.generate_latest(reg), {"Content-Type": prom.CONTENT_TYPE_LATEST})

    return handler
