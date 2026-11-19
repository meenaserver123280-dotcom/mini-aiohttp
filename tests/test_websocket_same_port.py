"""WebSocket endpoints registered on the HTTP server's own port."""
from __future__ import annotations

import asyncio

import pytest

from mini_aiohttp import Response, Server, WebSocket


async def _raw(port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return data


@pytest.fixture
async def app():
    server = Server()

    @server.route("/hello")
    async def hello(req):
        return Response(200, "hi")

    @server.websocket("/ws/{room}")
    async def echo(ws, req):
        await ws.send(f"room={req.params['room']}")
        while True:
            msg = await ws.recv()
            if msg is None:
                break
            await ws.send(msg)

    await server.start("127.0.0.1", 0)
    yield server
    await server.close(grace=0.5)


async def test_websocket_echo_with_path_params(app):
    ws = await WebSocket.connect(f"ws://127.0.0.1:{app.port}/ws/lobby")
    assert await ws.recv() == "room=lobby"
    await ws.send("ping")
    assert await ws.recv() == "ping"
    await ws.close(1000, "bye")


async def test_plain_http_still_works_on_same_port(app):
    data = await _raw(app.port, b"GET /hello HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert data.startswith(b"HTTP/1.1 200 OK")
    assert data.endswith(b"hi")


async def test_upgrade_to_unknown_path_is_plain_404(app):
    reader, writer = await asyncio.open_connection("127.0.0.1", app.port)
    writer.write(
        b"GET /nope HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    await writer.drain()
    status = await asyncio.wait_for(reader.readline(), 5)
    writer.close()
    assert status.startswith(b"HTTP/1.1 404")


async def test_bad_handshake_gets_400(app):
    data = await _raw(
        app.port,
        b"GET /ws/lobby HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Version: 13\r\n\r\n",
    )
    assert data.startswith(b"HTTP/1.1 400")
