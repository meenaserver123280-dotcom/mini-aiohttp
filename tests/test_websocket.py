import asyncio

import pytest

from mini_aiohttp import WebSocket, WebSocketServer, WebSocketError


async def test_websocket_client_server_text_binary_ping_and_close():
    received = []
    paths = []

    async def handler(ws, path):
        # Record instead of asserting inside the handler: an exception here would be
        # swallowed by the server and the client would wait forever on recv().
        paths.append(path)
        received.append(await ws.recv())
        await ws.send("reply")
        received.append(await ws.recv())
        await ws.send(b"binary-reply")

    server = WebSocketServer(handler)
    await server.start("127.0.0.1", 0)
    port = server._server.sockets[0].getsockname()[1]
    try:
        ws = await asyncio.wait_for(
            WebSocket.connect(f"ws://127.0.0.1:{port}/chat?room=1"), 5
        )
        await ws.send("hello")
        assert await ws.recv() == "reply"
        await ws.send(b"binary")
        assert await ws.recv() == b"binary-reply"
        await ws.close()
        assert paths == ["/chat?room=1"]
        assert received == ["hello", b"binary"]
    finally:
        await server.close()


async def test_websocket_rejects_reserved_opcode():
    async def peer(reader, writer):
        # An unmasked reserved opcode is invalid from a server to a client.
        writer.write(bytes((0x83, 0x00)))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(peer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        ws = WebSocket(reader, writer, client=True)
        with pytest.raises(WebSocketError, match="opcode"):
            await ws.recv()
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


async def test_websocket_connect_timeout_raises_and_does_not_hang():
    # A listener that accepts TCP but never answers the upgrade.
    open_writers = []

    async def silent(reader, writer):
        open_writers.append(writer)
        await asyncio.sleep(5)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(asyncio.TimeoutError):
            await WebSocket.connect(f"ws://127.0.0.1:{port}/", timeout=0.2)
    finally:
        # Python 3.12+ Server.wait_closed() waits for open connections, so close
        # the server-side sockets first or the test waits for them forever.
        for writer in open_writers:
            writer.close()
        server.close()
        await asyncio.wait_for(server.wait_closed(), 2)


def test_websocket_close_reason_is_not_silently_truncated():
    class DummyWriter:
        def __init__(self):
            self.data = bytearray()
        def write(self, data):
            self.data.extend(data)
        async def drain(self):
            pass
        def close(self):
            pass
        async def wait_closed(self):
            pass

    async def run():
        writer = DummyWriter()
        ws = WebSocket(None, writer, client=True)
        with pytest.raises(ValueError, match="123"):
            await ws.close(reason="x" * 124)

    asyncio.run(run())
