import pytest

from mini_aiohttp import ClientSession, Response, Server, fetch_all, gather_requests


@pytest.fixture
async def server():
    srv = Server()

    @srv.route("/a")
    async def a(req):
        return Response(200, "A")

    @srv.route("/b")
    async def b(req):
        return Response(200, "B")

    @srv.route("/echo", method="POST")
    async def echo(req):
        return Response(200, req.body)

    await srv.start("127.0.0.1", 0)
    yield srv
    await srv.close()


async def test_fetch_all_keeps_input_order(server):
    base = f"http://127.0.0.1:{server.port}"
    results = await fetch_all([f"{base}/a", f"{base}/b", f"{base}/a"])
    assert [r.body for r in results] == [b"A", b"B", b"A"]


async def test_mixed_specs(server):
    base = f"http://127.0.0.1:{server.port}"
    specs = [
        {"method": "POST", "url": f"{base}/echo", "data": "hi"},
        f"{base}/missing",
        ("GET", f"{base}/b"),
    ]
    async with ClientSession() as s:
        results = await gather_requests(s, specs, concurrency=2)
    assert results[0].body == b"hi"
    assert results[1].status == 404
    assert results[2].body == b"B"


async def test_one_failure_does_not_stop_others(server):
    base = f"http://127.0.0.1:{server.port}"
    # Port 1 refuses connections, so that request fails on its own.
    results = await fetch_all(["http://127.0.0.1:1/", f"{base}/a"], timeout=3)
    assert isinstance(results[0], Exception)
    assert results[1].body == b"A"


async def test_many_requests_bounded_concurrency(server):
    base = f"http://127.0.0.1:{server.port}"
    results = await fetch_all([f"{base}/a"] * 500, concurrency=50)
    assert len(results) == 500
    assert all(r.status == 200 for r in results)
