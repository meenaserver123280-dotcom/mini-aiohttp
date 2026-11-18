import pytest

from mini_aiohttp import ClientSession, Response, Server


@pytest.fixture
async def server():
    srv = Server(read_timeout=5)

    @srv.route("/")
    async def index(req):
        return Response(200, "hello")

    @srv.route("/echo", method="POST")
    async def echo(req):
        return Response(200, req.body)

    @srv.route("/boom")
    async def boom(req):
        raise RuntimeError("fail on purpose")

    await srv.start("127.0.0.1", 0)
    yield srv
    await srv.close()


async def test_get_returns_200(server):
    async with ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{server.port}/")
    assert r.status == 200
    assert r.body == b"hello"


async def test_post_echoes_body(server):
    async with ClientSession() as s:
        r = await s.post(f"http://127.0.0.1:{server.port}/echo", data="ping")
    assert r.body == b"ping"


async def test_unknown_path_is_404(server):
    async with ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{server.port}/missing")
    assert r.status == 404


async def test_wrong_method_is_405(server):
    async with ClientSession() as s:
        r = await s.post(f"http://127.0.0.1:{server.port}/", data="x")
    assert r.status == 405


async def test_handler_exception_is_500(server):
    async with ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{server.port}/boom")
    assert r.status == 500


async def test_connection_cap_returns_503():
    srv = Server(max_connections=0)
    await srv.start("127.0.0.1", 0)
    try:
        async with ClientSession() as s:
            r = await s.get(f"http://127.0.0.1:{srv.port}/")
        assert r.status == 503
    finally:
        await srv.close()
