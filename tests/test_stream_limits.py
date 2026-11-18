import asyncio
import gzip

import pytest

from mini_aiohttp import ClientError, ClientSession


def _chunked(data: bytes) -> bytes:
    return f"{len(data):x}\r\n".encode("ascii") + data + b"\r\n0\r\n\r\n"


async def _raw_server(response: bytes):
    """Serve one fixed raw HTTP response per connection, then close."""
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(response)
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(handler, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def test_stream_below_limit_passes():
    body = b"x" * 100
    srv, port = await _raw_server(
        b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n" + body
    )
    async with srv:
        async with ClientSession(max_response_size=1024) as s:
            r = await s.get(f"http://127.0.0.1:{port}/", stream=True)
            data = b"".join([c async for c in r.iter_chunks()])
    assert data == body


async def test_stream_exceeding_limit_raises():
    body = b"x" * 2048
    srv, port = await _raw_server(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        + _chunked(body)
    )
    async with srv:
        async with ClientSession(max_response_size=1024) as s:
            r = await s.get(f"http://127.0.0.1:{port}/", stream=True)
            with pytest.raises(ClientError):
                async for _ in r.iter_chunks():
                    pass


async def test_compressed_stream_exceeding_decoded_limit_raises():
    # Compressed body stays tiny; the decoded output is 1 MiB, far above the limit.
    compressed = gzip.compress(b"a" * (1024 * 1024))
    assert len(compressed) < 64 * 1024
    srv, port = await _raw_server(
        b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nTransfer-Encoding: chunked\r\n"
        b"Connection: close\r\n\r\n" + _chunked(compressed)
    )
    async with srv:
        async with ClientSession(max_response_size=64 * 1024) as s:
            r = await s.get(f"http://127.0.0.1:{port}/", stream=True)
            with pytest.raises(ClientError):
                async for _ in r.iter_chunks():
                    pass
