import asyncio

import pytest

from mini_aiohttp import ClientSession, Response, Server


async def test_streaming_response_is_chunked_and_not_buffered():
    srv = Server()

    async def chunks():
        yield b"first"
        await asyncio.sleep(0)
        yield " second"

    @srv.route("/events")
    async def events(req):
        return Response.stream(chunks(), headers={"Content-Type": "text/event-stream"})

    await srv.start("127.0.0.1", 0)
    try:
        async with ClientSession() as session:
            response = await session.get(f"http://127.0.0.1:{srv.port}/events")
        assert response.status == 200
        assert response.body == b"first second"
        assert response.headers.get("transfer-encoding") == "chunked"
        assert response.headers.get("content-type") == "text/event-stream"
    finally:
        await srv.close()


async def test_streaming_response_rejects_non_async_iterable():
    with pytest.raises(TypeError):
        Response.stream(b"not-an-async-iterator")


async def test_server_stats_count_requests_and_statuses():
    srv = Server()

    @srv.route("/ok")
    async def ok(req):
        return Response(201, "created")

    await srv.start("127.0.0.1", 0)
    try:
        async with ClientSession() as session:
            await session.get(f"http://127.0.0.1:{srv.port}/ok")
            await session.get(f"http://127.0.0.1:{srv.port}/missing")
        stats = srv.stats
        assert stats["total_requests"] == 2
        assert stats["status_counts"] == {201: 1, 404: 1}
        assert stats["total_connections"] >= 1
        assert stats["active_connections"] >= 0
    finally:
        await srv.close()
