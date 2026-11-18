import asyncio

import pytest

from mini_aiohttp import ClientError, ClientSession, Response, Server
from mini_aiohttp.client import DEFAULT_MAX_RESPONSE_SIZE, _host_header


@pytest.fixture
async def server():
    srv = Server()

    @srv.route("/")
    async def index(req):
        return Response(200, "ok")

    await srv.start("127.0.0.1", 0)
    yield srv
    await srv.close()


def serve_raw(handler):
    """Start a raw asyncio server that runs `handler(reader, writer)` per connection."""
    return asyncio.start_server(handler, "127.0.0.1", 0)


def body_handler(payload: bytes):
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        writer.close()
    return handler


async def test_header_injection_rejected_before_sending(server):
    async with ClientSession() as s:
        with pytest.raises(ValueError):
            await s.get(f"http://127.0.0.1:{server.port}/", headers={"X-A": "1\r\nX-Evil: 1"})


async def test_invalid_method_rejected(server):
    async with ClientSession() as s:
        with pytest.raises(ValueError):
            await s.request("GE T", f"http://127.0.0.1:{server.port}/")


def test_default_response_limit_is_1gib():
    assert ClientSession().max_response_size == DEFAULT_MAX_RESPONSE_SIZE == 1024 ** 3


async def test_response_over_limit_raises():
    srv = await serve_raw(body_handler(b"x" * (2 * 1024 * 1024)))
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        async with ClientSession(max_response_size=1024) as s:
            with pytest.raises(ClientError):
                await s.get(f"http://127.0.0.1:{port}/")


async def test_unlimited_response_size_is_opt_in():
    payload = b"x" * (2 * 1024 * 1024)
    srv = await serve_raw(body_handler(payload))
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        async with ClientSession(max_response_size=None) as s:
            r = await s.get(f"http://127.0.0.1:{port}/")
    assert len(r.body) == len(payload)


async def test_interim_100_continue_is_skipped():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 100 Continue\r\n\r\n"
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        )
        await writer.drain()
        writer.close()

    srv = await serve_raw(handler)
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        async with ClientSession() as s:
            r = await s.get(f"http://127.0.0.1:{port}/")
    assert r.status == 200
    assert r.body == b"ok"


async def test_duplicate_content_length_from_server_rejected():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 5\r\nConnection: close\r\n\r\nok"
        )
        await writer.drain()
        writer.close()

    srv = await serve_raw(handler)
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        async with ClientSession() as s:
            with pytest.raises(ClientError):
                await s.get(f"http://127.0.0.1:{port}/")


async def one_shot(reader, writer):
    """Answer one request and close, but advertise keep-alive, so the client pools a dead socket."""
    head = await reader.readuntil(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    if length:
        await reader.readexactly(length)
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok")
    await writer.drain()
    writer.close()


async def test_post_is_not_replayed_on_stale_socket():
    srv = await serve_raw(one_shot)
    port = srv.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    async with srv:
        async with ClientSession() as s:
            await s.get(url)          # pools a socket the server already closed
            await asyncio.sleep(0.05)
            with pytest.raises(ClientError):
                await s.post(url, data="x")


async def test_get_is_retried_on_stale_socket():
    srv = await serve_raw(one_shot)
    port = srv.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    async with srv:
        async with ClientSession() as s:
            await s.get(url)
            await asyncio.sleep(0.05)
            r = await s.get(url)
    assert r.body == b"ok"


def test_ipv6_host_header_has_brackets():
    assert _host_header("::1", 8080, False) == "[::1]:8080"
    assert _host_header("::1", 80, False) == "[::1]"
    assert _host_header("example.com", 443, True) == "example.com"
    assert _host_header("example.com", 8443, True) == "example.com:8443"


async def test_zero_timeout_is_respected(server):
    async with ClientSession() as s:
        with pytest.raises(asyncio.TimeoutError):
            await s.get(f"http://127.0.0.1:{server.port}/", timeout=0)
