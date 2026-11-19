"""HTTP/2 server: concurrent streams, the stream limit and the body limit."""
from __future__ import annotations

import asyncio
import shutil
import ssl
import subprocess

import pytest

from mini_aiohttp import HTTP2Server

pytest.importorskip("h2")
import h2.config  # noqa: E402
import h2.connection  # noqa: E402
import h2.events  # noqa: E402


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


async def _multiplex(port, cafile, requests):
    """Send several requests on one connection. Return [(path, 'ok' | 'reset')] in finish order.

    `requests` is a list of (path, body_bytes_or_None).
    """
    context = ssl.create_default_context(cafile=cafile)
    context.set_alpn_protocols(["h2"])
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname="localhost")
    conn = h2.connection.H2Connection(h2.config.H2Configuration(client_side=True, header_encoding="utf-8"))
    conn.initiate_connection()
    ids, pending_bodies = {}, {}
    for path, body in requests:
        sid = conn.get_next_available_stream_id()
        conn.send_headers(
            sid,
            [(":method", "POST" if body else "GET"), (":scheme", "https"),
             (":authority", "localhost"), (":path", path)],
            end_stream=body is None,
        )
        ids[sid] = path
        if body is not None:
            pending_bodies[sid] = body
    writer.write(conn.data_to_send())
    await writer.drain()
    for sid, body in pending_bodies.items():
        conn.send_data(sid, body, end_stream=True)
    writer.write(conn.data_to_send())
    await writer.drain()

    order = []
    while len(order) < len(requests):
        data = await asyncio.wait_for(reader.read(65536), 5)
        if not data:
            break
        for event in conn.receive_data(data):
            if isinstance(event, h2.events.StreamEnded):
                order.append((ids[event.stream_id], "ok"))
            elif isinstance(event, h2.events.StreamReset):
                order.append((ids[event.stream_id], "reset"))
            elif isinstance(event, h2.events.DataReceived):
                conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
        writer.write(conn.data_to_send())
        await writer.drain()
    writer.close()
    return order


async def _server(tmp_path, handler, **options):
    cert, key = certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server = HTTP2Server(handler, ssl_context=context, **options)
    await server.start("127.0.0.1", 0)
    port = server._server.sockets[0].getsockname()[1]
    return server, port, cert


async def test_slow_stream_does_not_block_fast_stream(tmp_path):
    async def handler(method, path, headers, body):
        if path == "/slow":
            await asyncio.sleep(0.5)
        return 200, {}, path

    server, port, cert = await _server(tmp_path, handler)
    try:
        order = await _multiplex(port, cert, [("/slow", None), ("/fast", None)])
    finally:
        await server.close()
    assert order == [("/fast", "ok"), ("/slow", "ok")]


async def test_exceeding_the_stream_limit_sends_goaway(tmp_path):
    async def handler(method, path, headers, body):
        await asyncio.sleep(0.5)
        return 200, {}, path

    server, port, cert = await _server(tmp_path, handler, max_concurrent_streams=1)
    try:
        context = ssl.create_default_context(cafile=cert)
        context.set_alpn_protocols(["h2"])
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname="localhost")
        conn = h2.connection.H2Connection(h2.config.H2Configuration(client_side=True, header_encoding="utf-8"))
        conn.initiate_connection()
        for path in ("/first", "/second"):
            sid = conn.get_next_available_stream_id()
            conn.send_headers(sid, [(":method", "GET"), (":scheme", "https"),
                                    (":authority", "localhost"), (":path", path)], end_stream=True)
        writer.write(conn.data_to_send())
        await writer.drain()
        terminated = None
        while terminated is None:
            data = await asyncio.wait_for(reader.read(65536), 5)
            if not data:
                break
            for event in conn.receive_data(data):
                if isinstance(event, h2.events.ConnectionTerminated):
                    terminated = event
            writer.write(conn.data_to_send())
            await writer.drain()
        writer.close()
    finally:
        await server.close()
    assert terminated is not None, "client never received GOAWAY"
    assert terminated.error_code == 1  # PROTOCOL_ERROR


async def test_oversized_body_resets_only_that_stream(tmp_path):
    async def handler(method, path, headers, body):
        return 200, {}, str(len(body))

    server, port, cert = await _server(tmp_path, handler, max_body_size=1024)
    try:
        order = dict(await _multiplex(port, cert, [("/big", b"x" * 2000), ("/small", b"y" * 10)]))
    finally:
        await server.close()
    assert order == {"/big": "reset", "/small": "ok"}


async def test_max_concurrent_streams_must_be_positive(tmp_path):
    cert, key = certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    with pytest.raises(ValueError):
        HTTP2Server(lambda *a: None, ssl_context=context, max_concurrent_streams=0)
