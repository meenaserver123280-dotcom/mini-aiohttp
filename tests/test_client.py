import asyncio

import pytest

from mini_aiohttp import ClientError, ClientSession, Response, Server


@pytest.fixture
async def server():
    srv = Server()

    @srv.route("/")
    async def index(req):
        return Response(200, "ok")

    await srv.start("127.0.0.1", 0)
    yield srv
    await srv.close()


async def test_keep_alive_reuses_connection(server):
    async with ClientSession() as s:
        await s.get(f"http://127.0.0.1:{server.port}/")
        await s.get(f"http://127.0.0.1:{server.port}/")
        assert len(s._idle[(False, "127.0.0.1", server.port)]) == 1


async def test_many_concurrent_requests(server):
    url = f"http://127.0.0.1:{server.port}/"
    async with ClientSession() as s:
        results = await asyncio.gather(*(s.get(url) for _ in range(200)))
    assert all(r.status == 200 for r in results)


async def test_reads_chunked_response():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
        )
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        async with ClientSession() as s:
            r = await s.get(f"http://127.0.0.1:{port}/")
    assert r.body == b"hello world"


async def test_closed_session_raises():
    s = ClientSession()
    await s.close()
    with pytest.raises(ClientError):
        await s.get("http://127.0.0.1:1/")
