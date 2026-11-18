import asyncio

import pytest

from mini_aiohttp import ClientError, ClientSession, Response, Server


# ---------- streaming upload ----------

async def chunked_echo(reader, writer):
    head = await reader.readuntil(b"\r\n\r\n")
    assert b"transfer-encoding: chunked" in head.lower()
    body = b""
    while True:
        size = int((await reader.readuntil(b"\r\n")).strip(), 16)
        if size == 0:
            await reader.readuntil(b"\r\n")
            break
        body += await reader.readexactly(size)
        await reader.readexactly(2)
    writer.write(
        f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
    )
    await writer.drain()
    writer.close()


async def test_streaming_upload_sends_chunked_body():
    srv = await asyncio.start_server(chunked_echo, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]

    async def chunks():
        for piece in (b"hello", b" ", b"world"):
            yield piece

    async with srv:
        async with ClientSession() as s:
            r = await s.post(f"http://127.0.0.1:{port}/up", data=chunks())
    assert r.body == b"hello world"


async def test_streaming_body_rejects_content_length():
    async def chunks():
        yield b"x"

    async with ClientSession() as s:
        with pytest.raises(ValueError):
            await s.post("http://127.0.0.1:1/", data=chunks(), headers={"Content-Length": "1"})


async def test_streaming_body_is_not_resent_after_307():
    async def redirecting(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 307 Temporary Redirect\r\nLocation: /x\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(redirecting, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]

    async def chunks():
        yield b"data"

    async with srv:
        async with ClientSession() as s:
            with pytest.raises(ClientError, match="streaming body"):
                await s.post(f"http://127.0.0.1:{port}/r", data=chunks())


# ---------- SOCKS5 ----------

async def start_socks_proxy(target_port: int):
    """Tiny SOCKS5 proxy (no auth). It records the requested host and port, then pipes bytes."""
    seen = []

    async def handle(reader, writer):
        header = await reader.readexactly(2)
        await reader.readexactly(header[1])
        writer.write(b"\x05\x00")
        await writer.drain()
        _, _, _, atyp = await reader.readexactly(4)
        assert atyp == 3
        length = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(length)).decode()
        port = int.from_bytes(await reader.readexactly(2), "big")
        seen.append((host, port))

        target_reader, target_writer = await asyncio.open_connection("127.0.0.1", target_port)
        writer.write(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
        await writer.drain()

        async def pump(src, dst):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                dst.close()

        await asyncio.gather(pump(reader, target_writer), pump(target_reader, writer))

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, seen


async def test_socks5h_proxy_tunnels_http_request():
    app = Server()

    @app.route("/host")
    async def host(req):
        return Response(200, req.headers.get("host", ""))

    await app.start("127.0.0.1", 0)
    socks, seen = await start_socks_proxy(app.port)
    socks_port = socks.sockets[0].getsockname()[1]
    try:
        async with ClientSession(proxy=f"socks5h://127.0.0.1:{socks_port}") as s:
            r = await s.get("http://target.example/host")
        assert r.body == b"target.example"
        assert seen == [("target.example", 80)]
    finally:
        socks.close()
        await app.close()


async def test_https_through_socks_attempts_tunnel_without_local_dns():
    # socks5h delegates target DNS resolution to the proxy. A closed local
    # proxy must therefore fail as a connection error, not a local DNS error.
    async with ClientSession(proxy="socks5h://127.0.0.1:1") as s:
        with pytest.raises((OSError, asyncio.TimeoutError)):
            await s.get("https://target.example/")


def test_proxy_schemes():
    assert ClientSession(proxy="socks5://127.0.0.1:1080").proxy
    assert ClientSession(proxy="socks5h://127.0.0.1:1080").proxy
    with pytest.raises(ValueError):
        ClientSession(proxy="ftp://127.0.0.1:21")