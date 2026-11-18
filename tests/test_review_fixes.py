import pytest

from mini_aiohttp.response import Request, Response
from mini_aiohttp.security import _normalize_address, cors, ip_policy, require_json
import ipaddress


def _req(client=None, headers=None, body=b"", method="GET"):
    r = Request(method, "/", "", "HTTP/1.1", headers or {}, body)
    r.client = client
    return r


async def _ok(req):
    return Response(200, "ok")


async def test_ip_policy_blocks_ipv4_mapped_ipv6():
    mw = ip_policy(deny=["203.0.113.0/24"])
    resp = await mw(_req(client="::ffff:203.0.113.5"), _ok)
    assert resp.status == 403


async def test_ip_policy_allows_other_clients():
    mw = ip_policy(deny=["203.0.113.0/24"])
    resp = await mw(_req(client="198.51.100.7"), _ok)
    assert resp.status == 200


def test_normalize_address_maps_ipv4_mapped():
    assert _normalize_address(ipaddress.ip_address("::ffff:10.0.0.1")) == ipaddress.ip_address("10.0.0.1")
    assert _normalize_address(ipaddress.ip_address("2001:db8::1")) == ipaddress.ip_address("2001:db8::1")


async def test_cors_appends_vary_instead_of_overwriting():
    async def handler(req):
        return Response(200, "x", {"Vary": "Accept-Encoding"})

    mw = cors(allow_origins=["https://a.example"])
    resp = await mw(_req(headers={"origin": "https://a.example"}), handler)
    tokens = [t.strip() for t in resp.headers["vary"].split(",")]
    assert "Accept-Encoding" in tokens and "Origin" in tokens


async def test_require_json_accepts_tuple_types_and_reports_name():
    async def handler(req):
        return Response(200, "ok")

    wrapped = require_json({"score": (int, float)})(handler)
    ok = await wrapped(_req(body=b'{"score": 1.5}', method="POST"))
    assert ok.status == 200
    bad = await wrapped(_req(body=b'{"score": "x"}', method="POST"))
    assert bad.status == 400
    assert b"int or float" in bad.body


async def test_require_json_rejects_bool_for_int():
    async def handler(req):
        return Response(200, "ok")

    wrapped = require_json({"n": int})(handler)
    resp = await wrapped(_req(body=b'{"n": true}', method="POST"))
    assert resp.status == 400


def test_metrics_middleware_can_be_created_twice():
    prom = pytest.importorskip("prometheus_client")
    from mini_aiohttp.extras.metrics import prometheus_middleware

    reg = prom.CollectorRegistry()
    prometheus_middleware(registry=reg)
    prometheus_middleware(registry=reg)  # must not raise "Duplicated timeseries"
