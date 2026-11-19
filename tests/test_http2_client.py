"""HTTP/2 client: one pooled connection per origin, parallel streams, stream-limit waiting."""
from __future__ import annotations

import asyncio
import shutil
import ssl
import subprocess
import time

import pytest

from mini_aiohttp import HTTP2Client, HTTP2Server

pytest.importorskip("h2")


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
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server = HTTP2Server(handler, ssl_context=context, **options)
    await server.start("127.0.0.1", 0)
    return server, server.port, cert


def _client(cert):
    context = ssl.create_default_context(cafile=str(cert))
    return HTTP2Client(ssl_context=context, timeout=5)


async def test_parallel_requests_share_one_connection(tmp_path):
    async def handler(method, path, headers, body):
        await asyncio.sleep(0.3)
        return 200, {}, path

    server, port, cert = await _server(tmp_path, handler)
    client = _client(cert)
    try:
        started = time.monotonic()
        results = await asyncio.gather(*(
            client.request("GET", f"https://localhost:{port}/item{i}") for i in range(5)
        ))
        elapsed = time.monotonic() - started
        assert len(server._connections) == 1, "requests did not reuse one connection"
    finally:
        await client.close()
        await server.close()
    assert [status for status, _, _ in results] == [200] * 5
    assert [body for _, _, body in results] == [f"/item{i}".encode() for i in range(5)]
    assert elapsed < 1.2, f"streams ran one after another ({elapsed:.2f}s)"


async def test_requests_wait_for_a_free_stream_slot(tmp_path):
    async def handler(method, path, headers, body):
        await asyncio.sleep(0.2)
        return 200, {}, path

    server, port, cert = await _server(tmp_path, handler, max_concurrent_streams=2)
    client = _client(cert)
    try:
        results = await asyncio.gather(*(
            client.request("GET", f"https://localhost:{port}/n{i}") for i in range(6)
        ))
    finally:
        await client.close()
        await server.close()
    assert sorted(status for status, _, _ in results) == [200] * 6


async def test_connection_is_reopened_after_close(tmp_path):
    async def handler(method, path, headers, body):
        return 200, {}, "ok"

    server, port, cert = await _server(tmp_path, handler)
    client = _client(cert)
    try:
        status, _, _ = await client.request("GET", f"https://localhost:{port}/a")
        await client.close()
        status2, _, body = await client.request("GET", f"https://localhost:{port}/b")
    finally:
        await client.close()
        await server.close()
    assert (status, status2, body) == (200, 200, b"ok")
