from __future__ import annotations

import asyncio
import gzip
import json
import zlib

import pytest

from mini_aiohttp import ClientError, ClientSession, Response, use_uvloop
from mini_aiohttp._encoding import ACCEPT_ENCODING, ContentDecoder


def serve_bytes(body: bytes, extra_headers: bytes = b"", seen: list | None = None):
    async def handler(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        if seen is not None:
            seen.append(head)
        writer.write(
            b"HTTP/1.1 200 OK\r\n" + extra_headers
            + b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body
        )
        await writer.drain()
        writer.close()
    return handler


async def fetch(body, extra_headers=b"", stream=False, seen=None):
    srv = await asyncio.start_server(serve_bytes(body, extra_headers, seen), "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as s:
            resp = await s.get(f"http://127.0.0.1:{port}/", stream=stream)
            if stream:
                data = b"".join([c async for c in resp.iter_chunks()])
                return resp, data
            return resp, resp.body
    finally:
        srv.close()
        await asyncio.wait_for(srv.wait_closed(), 2)


async def test_gzip_body_is_decoded():
    payload = b"hello gzip " * 200
    resp, body = await fetch(gzip.compress(payload), b"Content-Encoding: gzip\r\n")
    assert body == payload
    assert "content-encoding" not in resp.headers


async def test_deflate_zlib_body_is_decoded():
    payload = b"deflate data " * 100
    _, body = await fetch(zlib.compress(payload), b"Content-Encoding: deflate\r\n")
    assert body == payload


async def test_deflate_raw_body_is_decoded():
    payload = b"raw deflate " * 100
    raw = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    body_bytes = raw.compress(payload) + raw.flush()
    _, body = await fetch(body_bytes, b"Content-Encoding: deflate\r\n")
    assert body == payload


async def test_streamed_gzip_body_is_decoded():
    payload = b"streamed " * 500
    _, body = await fetch(gzip.compress(payload), b"Content-Encoding: gzip\r\n", stream=True)
    assert body == payload


async def test_accept_encoding_is_sent_by_default():
    seen = []
    await fetch(b"x", seen=seen)
    assert ("Accept-Encoding: " + ACCEPT_ENCODING).encode() in seen[0]


async def test_unknown_content_encoding_raises():
    srv = await asyncio.start_server(serve_bytes(b"abc", b"Content-Encoding: compress\r\n"), "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as s:
            with pytest.raises(ClientError, match="Unsupported Content-Encoding"):
                await s.get(f"http://127.0.0.1:{port}/")
    finally:
        srv.close()
        await asyncio.wait_for(srv.wait_closed(), 2)


def test_decoder_identity_and_empty_are_noops():
    assert not ContentDecoder("identity").active
    assert not ContentDecoder(None).active
    assert not ContentDecoder("").active


def test_malformed_gzip_is_client_error():
    d = ContentDecoder("gzip")
    with pytest.raises(ClientError):
        d.feed(b"not gzip at all") + d.finish()


def test_response_json_roundtrip():
    resp = Response.json({"a": 1, "name": "नमस्ते"})
    assert json.loads(resp.body) == {"a": 1, "name": "नमस्ते"}


def test_use_uvloop_returns_bool():
    # Does not fail whether or not uvloop is installed.
    assert isinstance(use_uvloop(), bool)
    # Restore the default policy so other tests are unaffected.
    asyncio.set_event_loop_policy(None)
