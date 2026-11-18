import gzip

import pytest

from mini_aiohttp.compression import _accepts_gzip, gzip_middleware
from mini_aiohttp.response import Request, Response


async def _run(mw, resp, accept="gzip"):
    async def call_next(req):
        return resp

    req = Request("GET", "/", "", "HTTP/1.1", {"accept-encoding": accept} if accept else {})
    return await mw(req, call_next)


@pytest.mark.parametrize("header, expected", [
    ("gzip", True),
    ("deflate, gzip;q=0.5", True),
    ("gzip;q=0", False),
    ("*", True),
    ("br", False),
    ("", False),
])
def test_accept_encoding_parsing(header, expected):
    assert _accepts_gzip(header) is expected


async def test_large_text_response_is_gzipped():
    body = b"hello " * 500
    resp = await _run(gzip_middleware(min_size=100), Response(200, body, {"Content-Type": "text/plain"}))
    assert resp.headers["content-encoding"] == "gzip"
    assert gzip.decompress(resp.body) == body
    assert "accept-encoding" in resp.headers["vary"].lower()


async def test_small_response_is_untouched():
    resp = await _run(gzip_middleware(min_size=1000), Response(200, b"tiny", {"Content-Type": "text/plain"}))
    assert resp.body == b"tiny"
    assert "content-encoding" not in resp.headers


async def test_client_without_gzip_gets_identity():
    body = b"x" * 5000
    resp = await _run(gzip_middleware(), Response(200, body, {"Content-Type": "text/plain"}), accept="")
    assert resp.body == body


async def test_binary_content_type_is_not_compressed():
    body = b"\x89PNG" * 2000
    resp = await _run(gzip_middleware(), Response(200, body, {"Content-Type": "image/png"}))
    assert resp.body == body
