import asyncio

import pytest

from mini_aiohttp import ClientSession, Response, Server


async def raw(port: int, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    return data


@pytest.fixture
async def chunked_server():
    srv = Server(max_body_size=8)

    @srv.route("/echo", method="POST")
    async def echo(req):
        return Response(200, req.body)

    await srv.start("127.0.0.1", 0)
    yield srv
    await srv.close()


async def test_chunked_request_body_is_decoded(chunked_server):
    payload = (
        b"POST /echo HTTP/1.1\r\nHost: localhost\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        b"3\r\nabc\r\n2;ext=yes\r\nde\r\n"
        b"0\r\nX-Checksum: ok\r\n\r\n"
    )
    response = await raw(chunked_server.port, payload)
    assert response.startswith(b"HTTP/1.1 200")
    assert response.endswith(b"abcde")


async def test_chunked_body_enforces_max_body_size(chunked_server):
    payload = (
        b"POST /echo HTTP/1.1\r\nHost: localhost\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        b"9\r\n123456789\r\n0\r\n\r\n"
    )
    response = await raw(chunked_server.port, payload)
    assert response.startswith(b"HTTP/1.1 413")


async def test_content_length_and_transfer_encoding_are_rejected(chunked_server):
    payload = (
        b"POST /echo HTTP/1.1\r\nHost: localhost\r\n"
        b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\n"
        b"Connection: close\r\n\r\n0\r\n\r\n"
    )
    response = await raw(chunked_server.port, payload)
    assert response.startswith(b"HTTP/1.1 400")


async def test_invalid_chunk_size_is_rejected(chunked_server):
    payload = (
        b"POST /echo HTTP/1.1\r\nHost: localhost\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        b"not-hex\r\n"
    )
    response = await raw(chunked_server.port, payload)
    assert response.startswith(b"HTTP/1.1 400")


async def test_forbidden_trailer_is_rejected(chunked_server):
    payload = (
        b"POST /echo HTTP/1.1\r\nHost: localhost\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        b"0\r\nContent-Length: 0\r\n\r\n"
    )
    response = await raw(chunked_server.port, payload)
    assert response.startswith(b"HTTP/1.1 400")


async def test_normal_content_length_still_works(chunked_server):
    async with ClientSession() as session:
        response = await session.post(
            f"http://127.0.0.1:{chunked_server.port}/echo", data=b"normal"
        )
    assert response.status == 200
    assert response.body == b"normal"