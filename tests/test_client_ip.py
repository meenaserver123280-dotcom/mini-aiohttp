"""Tests for the Cloudflare client IP middleware and its use with the rate limiter. No network access."""
from __future__ import annotations

import pytest

from mini_aiohttp import RateLimiter, Request, Response, cloudflare_client_ip


def make(peer, headers=None):
    req = Request("GET", "/", "", "HTTP/1.1", headers or {}, b"")
    req.client = peer
    return req


async def run(middleware, req):
    seen = {}

    async def call_next(r):
        seen["client"] = r.client
        seen["peer"] = r.peer_address
        return Response(200, "ok")

    await middleware(req, call_next)
    return seen


async def test_header_used_when_peer_is_cloudflare():
    seen = await run(cloudflare_client_ip(), make("172.64.1.1", {"cf-connecting-ip": "198.51.100.7"}))
    assert seen["client"] == "198.51.100.7"
    assert seen["peer"] == "172.64.1.1"


async def test_header_ignored_when_peer_is_not_cloudflare():
    seen = await run(cloudflare_client_ip(), make("203.0.113.5", {"cf-connecting-ip": "1.2.3.4"}))
    assert seen["client"] == "203.0.113.5"


async def test_malformed_header_keeps_peer():
    seen = await run(cloudflare_client_ip(), make("172.64.1.1", {"cf-connecting-ip": "not-an-ip"}))
    assert seen["client"] == "172.64.1.1"


async def test_missing_header_keeps_peer():
    seen = await run(cloudflare_client_ip(), make("172.64.1.1"))
    assert seen["client"] == "172.64.1.1"


async def test_ipv6_cloudflare_peer():
    seen = await run(cloudflare_client_ip(), make("2606:4700::1", {"cf-connecting-ip": "2001:db8::5"}))
    assert seen["client"] == "2001:db8::5"


async def test_ipv4_mapped_cloudflare_peer():
    seen = await run(cloudflare_client_ip(), make("::ffff:172.64.1.1", {"cf-connecting-ip": "198.51.100.9"}))
    assert seen["client"] == "198.51.100.9"


async def test_custom_networks_and_header():
    mw = cloudflare_client_ip(networks=["10.0.0.0/8"], header="X-Real-IP")
    seen = await run(mw, make("10.1.2.3", {"x-real-ip": "198.51.100.20"}))
    assert seen["client"] == "198.51.100.20"


def test_empty_networks_rejected():
    with pytest.raises(ValueError):
        cloudflare_client_ip(networks=[])


async def test_rate_limiter_counts_real_visitors_not_the_proxy():
    limiter = RateLimiter(rate=0.001, burst=1)
    limited = limiter.middleware()

    async def handler(req):
        return Response(200, "ok")

    async def through_limiter(req):
        return await limited(req, handler)

    cf = cloudflare_client_ip()
    first = await cf(make("172.64.1.1", {"cf-connecting-ip": "198.51.100.1"}), through_limiter)
    other = await cf(make("172.64.1.1", {"cf-connecting-ip": "198.51.100.2"}), through_limiter)
    again = await cf(make("172.64.1.1", {"cf-connecting-ip": "198.51.100.1"}), through_limiter)
    assert first.status == 200
    assert other.status == 200
    assert again.status == 429
