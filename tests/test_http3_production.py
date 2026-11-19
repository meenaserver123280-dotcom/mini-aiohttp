"""HTTP/3: pooled connections, parallel streams, per-stream limits and graceful close."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time

import pytest

from mini_aiohttp import HTTP3Client, HTTP3Server

pytest.importorskip("aioquic")


def certificate(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("openssl is not installed")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], check=True, capture_output=True)
    return cert, key


async def _server(tmp_path, handler, **options):
    cert, key = certificate(tmp_path)
    server = HTTP3Server(handler, certificate=str(cert), private_key=str(key), **options)
    await server.start("127.0.0.1", 0)
    return server, server.port


async def test_parallel_requests_reuse_one_connection(tmp_path):
    async def handler(method, path, headers, body):
        await asyncio.sleep(0.3)
        return 200, {}, path

    server, port = await _server(tmp_path, handler)
    client = HTTP3Client(verify=False, timeout=5)
    try:
        started = time.monotonic()
        results = await asyncio.gather(*(
            client.request("GET", f"https://127.0.0.1:{port}/p{i}") for i in range(5)
        ))
        elapsed = time.monotonic() - started
        assert len(server._protocols) == 1, "requests did not reuse one QUIC connection"
    finally:
        await client.close()
        await server.close()
    assert [body for _, _, body in results] == [f"/p{i}".encode() for i in range(5)]
    assert elapsed < 1.2, f"streams ran one after another ({elapsed:.2f}s)"


async def test_oversized_request_headers_fail_only_that_request(tmp_path):
    async def handler(method, path, headers, body):
        return 200, {}, "ok"

    server, port = await _server(tmp_path, handler, max_header_list_size=512)
    client = HTTP3Client(verify=False, timeout=5)
    try:
        with pytest.raises(ConnectionError):
            await client.request("GET", f"https://127.0.0.1:{port}/big", headers={"x-big": "a" * 2000})
        status, _, body = await client.request("GET", f"https://127.0.0.1:{port}/small")
    finally:
        await client.close()
        await server.close()
    assert (status, body) == (200, b"ok")


async def test_graceful_close_delivers_inflight_response(tmp_path):
    async def handler(method, path, headers, body):
        await asyncio.sleep(0.3)
        return 200, {}, "done"

    server, port = await _server(tmp_path, handler)
    client = HTTP3Client(verify=False, timeout=5)
    try:
        request = asyncio.ensure_future(client.request("GET", f"https://127.0.0.1:{port}/work"))
        await asyncio.sleep(0.1)
        await server.close(grace=2.0)
        status, _, body = await request
    finally:
        await client.close()
    assert (status, body) == (200, b"done")


async def test_pending_request_fails_when_server_goes_away(tmp_path):
    async def handler(method, path, headers, body):
        await asyncio.sleep(10)
        return 200, {}, "late"

    server, port = await _server(tmp_path, handler)
    client = HTTP3Client(verify=False, timeout=5)
    try:
        request = asyncio.ensure_future(client.request("GET", f"https://127.0.0.1:{port}/hang"))
        await asyncio.sleep(0.1)
        started = time.monotonic()
        await server.close(grace=0.1)
        with pytest.raises(ConnectionError):
            await request
        assert time.monotonic() - started < 3, "pending request hung instead of failing"
    finally:
        await client.close()
