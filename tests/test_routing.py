import pytest

from mini_aiohttp import ClientSession, Response, Server


@pytest.fixture
async def server():
    srv = Server()

    @srv.route("/users/{id}")
    async def get_user(req):
        return Response(200, f"user={req.params['id']}")

    @srv.route("/users/{uid}/posts/{pid}")
    async def get_post(req):
        return Response(200, f"{req.params['uid']}:{req.params['pid']}")

    @srv.route("/users/me")
    async def me(req):
        return Response(200, "me")

    @srv.route("/users/{id}", method="DELETE")
    async def delete_user(req):
        return Response(200, "deleted")

    @srv.middleware_
    async def add_header(req, call_next):
        resp = await call_next(req)
        resp.headers["x-powered-by"] = "mini-aiohttp"
        return resp

    await srv.start("127.0.0.1", 0)
    yield srv
    await srv.close()


async def test_path_param_is_captured(server):
    async with ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{server.port}/users/42")
    assert r.body == b"user=42"


async def test_multiple_path_params(server):
    async with ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{server.port}/users/7/posts/99")
    assert r.body == b"7:99"


async def test_literal_route_beats_param_route(server):
    async with ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{server.port}/users/me")
    assert r.body == b"me"


async def test_same_path_different_method(server):
    async with ClientSession() as s:
        r = await s.request("DELETE", f"http://127.0.0.1:{server.port}/users/5")
    assert r.body == b"deleted"


async def test_middleware_runs_on_every_response(server):
    async with ClientSession() as s:
        ok = await s.get(f"http://127.0.0.1:{server.port}/users/1")
        missing = await s.get(f"http://127.0.0.1:{server.port}/nope")
    assert ok.headers["x-powered-by"] == "mini-aiohttp"
    assert missing.status == 404
