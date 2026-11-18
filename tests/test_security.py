import asyncio
import json

import pytest

from mini_aiohttp import ClientSession, Response, Server


async def raw(port: int, payload: bytes) -> bytes:
    """Send raw bytes and return everything the server sends back before it closes."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    return data


@pytest.fixture
async def server():
    srv = Server()

    @srv.route("/echo", method="POST")
    async def echo(req):
        return Response(200, req.body)

    @srv.route("/users/{id}")
    async def user(req):
        return Response(200, f"user={req.params['id']}")

    @srv.route("/files/{name}")
    async def files(req):
        return Response(200, req.params["name"])

    @srv.route("/q")
    async def q(req):
        return Response.json(req.query_params())

    @srv.route("/evil")
    async def evil(req):
        return Response(200, "x", {"X-A": "1\r\nX-Evil: 1"})

    await srv.start("127.0.0.1", 0)
    yield srv
    await srv.close()


async def test_duplicate_content_length_rejected(server):
    data = await raw(server.port, b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
    assert data.startswith(b"HTTP/1.1 400")


async def test_invalid_method_rejected(server):
    data = await raw(server.port, b"G@T / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert data.startswith(b"HTTP/1.1 400")


async def test_unsupported_http_version_rejected(server):
    data = await raw(server.port, b"GET / HTTP/2.0\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert data.startswith(b"HTTP/1.1 505")


async def test_obsolete_line_folding_rejected(server):
    data = await raw(server.port, b"GET / HTTP/1.1\r\nHost: x\r\nX-A: 1\r\n folded\r\nConnection: close\r\n\r\n")
    assert data.startswith(b"HTTP/1.1 400")


async def test_empty_chunked_request_is_accepted(server):
    data = await raw(server.port, b"POST /echo HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n0\r\n\r\n")
    assert data.startswith(b"HTTP/1.1 200")
    assert data.endswith(b"Content-Length: 0\r\nConnection: close\r\n\r\n")


async def test_header_name_with_space_before_colon_rejected(server):
    data = await raw(server.port, b"GET / HTTP/1.1\r\nHost : x\r\nConnection: close\r\n\r\n")
    assert data.startswith(b"HTTP/1.1 400")


async def test_oversized_body_rejected_without_reading_it():
    small = Server(max_body_size=10)
    await small.start("127.0.0.1", 0)
    try:
        data = await raw(
            small.port,
            b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 999999\r\nConnection: close\r\n\r\n",
        )
        assert data.startswith(b"HTTP/1.1 413")
    finally:
        await small.close()


async def test_absolute_form_target_is_routed(server):
    data = await raw(
        server.port,
        b"GET http://127.0.0.1/users/5 HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    )
    assert data.startswith(b"HTTP/1.1 200")
    assert data.endswith(b"user=5")


async def test_path_param_is_percent_decoded(server):
    data = await raw(server.port, b"GET /files/a%20b HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert data.endswith(b"a b")


async def test_encoded_slash_stays_in_one_segment(server):
    data = await raw(server.port, b"GET /files/a%2Fb HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert data.endswith(b"a/b")


async def test_query_params_parsed(server):
    async with ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{server.port}/q?a=1&a=2&b=")
    assert r.status == 200
    assert json.loads(r.body) == {"a": ["1", "2"], "b": [""]}


async def test_header_injection_from_handler_is_blocked(server):
    data = await raw(server.port, b"GET /evil HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert data.startswith(b"HTTP/1.1 500")
    assert b"X-Evil" not in data


def test_response_rejects_crlf_in_headers():
    with pytest.raises(ValueError):
        Response(200, "x", {"X-A": "a\r\nb"})


async def test_graceful_shutdown_finishes_inflight_request():
    srv = Server()

    @srv.route("/slow")
    async def slow(req):
        await asyncio.sleep(0.3)
        return Response(200, "done")

    await srv.start("127.0.0.1", 0)
    async with ClientSession() as s:
        task = asyncio.create_task(s.get(f"http://127.0.0.1:{srv.port}/slow"))
        await asyncio.sleep(0.1)
        await srv.close(grace=2)
        r = await task
    assert r.status == 200
    assert r.body == b"done"