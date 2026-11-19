"""ASGI adapter tests. They call the app directly with fake receive and send callables."""
from __future__ import annotations

import json

import pytest

from mini_aiohttp import ASGIApp, Response, Server


def _scope(method="GET", path="/", query=b"", headers=(), raw_path=None, kind="http"):
    return {
        "type": kind,
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": raw_path,
        "root_path": "",
        "query_string": query,
        "headers": list(headers),
        "client": ("203.0.113.7", 4000),
        "server": ("testserver", 80),
    }


class _Receiver:
    def __init__(self, *messages):
        self._messages = list(messages)

    async def __call__(self):
        if self._messages:
            return self._messages.pop(0)
        return {"type": "http.disconnect"}


class _Sender:
    def __init__(self):
        self.messages: list = []

    async def __call__(self, message):
        self.messages.append(message)

    @property
    def start(self):
        return next(m for m in self.messages if m["type"] == "http.response.start")

    @property
    def status(self):
        return self.start["status"]

    def header(self, name):
        raw = name.encode("latin-1")
        return [value.decode("latin-1") for key, value in self.start["headers"] if key == raw]

    @property
    def body(self):
        return b"".join(m.get("body", b"") for m in self.messages if m["type"] == "http.response.body")


async def _call(app, scope, body=b""):
    sender = _Sender()
    await app(scope, _Receiver({"type": "http.request", "body": body, "more_body": False}), sender)
    return sender


@pytest.fixture
def server():
    srv = Server()

    @srv.route("/")
    async def home(req):
        return "hello"

    @srv.route("/users/{user_id}")
    async def user(req):
        return Response.json({"id": req.params["user_id"]})

    @srv.route("/echo", method="POST")
    async def echo(req):
        return Response(200, req.body, {"Content-Type": "application/octet-stream"})

    @srv.route("/search")
    async def search(req):
        return Response.json(req.query_params())

    @srv.route("/boom")
    async def boom(req):
        raise RuntimeError("kaboom")

    @srv.route("/files/{name}")
    async def files(req):
        return req.params["name"]

    @srv.route("/cookies")
    async def cookies(req):
        resp = Response(200, "ok")
        resp.add_set_cookie("a=1; Path=/")
        resp.add_set_cookie("b=2; Path=/")
        return resp

    @srv.route("/numbers")
    async def numbers(req):
        async def gen():
            for i in range(3):
                yield f"{i}\n"

        return Response.stream(gen(), headers={"Content-Type": "text/plain"})

    return srv


async def test_get_returns_text(server):
    sender = await _call(ASGIApp(server), _scope(path="/"))
    assert sender.status == 200
    assert sender.body == b"hello"
    assert sender.header("content-length") == ["5"]
    assert sender.header("content-type") == ["text/plain; charset=utf-8"]


async def test_path_params(server):
    sender = await _call(ASGIApp(server), _scope(path="/users/42"))
    assert json.loads(sender.body) == {"id": "42"}


async def test_post_body_reaches_handler(server):
    sender = await _call(ASGIApp(server), _scope("POST", "/echo"), b"\x00payload")
    assert sender.status == 200
    assert sender.body == b"\x00payload"


async def test_query_string_is_parsed(server):
    sender = await _call(ASGIApp(server), _scope(path="/search", query=b"q=a&q=b"))
    assert json.loads(sender.body) == {"q": ["a", "b"]}


async def test_unknown_path_is_404(server):
    sender = await _call(ASGIApp(server), _scope(path="/missing"))
    assert sender.status == 404


async def test_wrong_method_is_405(server):
    sender = await _call(ASGIApp(server), _scope("POST", "/"))
    assert sender.status == 405


async def test_handler_exception_is_500_and_server_keeps_working(server):
    app = ASGIApp(server)
    sender = await _call(app, _scope(path="/boom"))
    assert sender.status == 500
    assert sender.body == b"Internal Server Error"
    assert (await _call(app, _scope(path="/"))).status == 200


async def test_raw_path_percent_escapes_are_decoded_once(server):
    sender = await _call(ASGIApp(server), _scope(path="/files/a b", raw_path=b"/files/a%20b"))
    assert sender.body == b"a b"


async def test_head_drops_body_but_keeps_length(server):
    sender = await _call(ASGIApp(server), _scope("HEAD", "/"))
    assert sender.status == 200
    assert sender.body == b""
    assert sender.header("content-length") == ["5"]


async def test_multiple_set_cookie_headers(server):
    sender = await _call(ASGIApp(server), _scope(path="/cookies"))
    assert sender.header("set-cookie") == ["a=1; Path=/", "b=2; Path=/"]


async def test_streaming_response_has_no_fixed_length(server):
    sender = await _call(ASGIApp(server), _scope(path="/numbers"))
    assert sender.body == b"0\n1\n2\n"
    assert sender.header("content-length") == []
    more = [m["more_body"] for m in sender.messages if m["type"] == "http.response.body"]
    assert all(more[:-1]) and more[-1] is False


async def test_streamed_head_sends_no_body(server):
    sender = await _call(ASGIApp(server), _scope("HEAD", "/numbers"))
    assert sender.status == 200
    assert sender.body == b""


async def test_middleware_runs_for_every_request_including_404(server):
    async def tag(req, call_next):
        resp = await call_next(req)
        resp.headers["x-tagged"] = "yes"
        return resp

    server.middleware_(tag)
    sender = await _call(ASGIApp(server), _scope(path="/nope"))
    assert sender.status == 404
    assert sender.header("x-tagged") == ["yes"]


async def test_body_over_limit_is_413():
    srv = Server(max_body_size=4)

    @srv.route("/", method="POST")
    async def handler(req):
        return "never"

    sender = await _call(ASGIApp(srv), _scope("POST", "/"), b"12345")
    assert sender.status == 413


async def test_chunked_body_limit_counts_running_total():
    srv = Server(max_body_size=4)
    sender = _Sender()
    receiver = _Receiver(
        {"type": "http.request", "body": b"123", "more_body": True},
        {"type": "http.request", "body": b"456", "more_body": False},
    )
    await ASGIApp(srv)(_scope("POST", "/"), receiver, sender)
    assert sender.status == 413


async def test_client_disconnect_sends_nothing(server):
    sender = _Sender()
    await ASGIApp(server)(_scope("POST", "/echo"), _Receiver({"type": "http.disconnect"}), sender)
    assert sender.messages == []


async def test_requests_are_counted_in_server_stats(server):
    app = ASGIApp(server)
    await _call(app, _scope(path="/"))
    await _call(app, _scope(path="/missing"))
    stats = server.stats
    assert stats["total_requests"] == 2
    assert stats["status_counts"] == {200: 1, 404: 1}


async def test_lifespan_runs_hooks_in_order(server):
    calls: list = []

    async def startup():
        calls.append("startup")

    async def shutdown():
        calls.append("shutdown")

    sender = _Sender()
    app = ASGIApp(server, on_startup=startup, on_shutdown=shutdown)
    await app(
        {"type": "lifespan", "asgi": {"version": "3.0"}},
        _Receiver({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}),
        sender,
    )
    assert calls == ["startup", "shutdown"]
    assert [m["type"] for m in sender.messages] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


async def test_failing_startup_hook_reports_failure():
    async def startup():
        raise RuntimeError("no database")

    sender = _Sender()
    app = ASGIApp(Server(), on_startup=startup)
    await app({"type": "lifespan", "asgi": {"version": "3.0"}}, _Receiver({"type": "lifespan.startup"}), sender)
    assert sender.messages == [{"type": "lifespan.startup.failed", "message": "no database"}]


async def test_websocket_scope_is_refused(server):
    sender = _Sender()
    await ASGIApp(server)(_scope(kind="websocket", path="/ws"), _Receiver(), sender)
    assert sender.messages == [{"type": "websocket.close", "code": 1000}]


async def test_unknown_scope_type_raises(server):
    with pytest.raises(ValueError):
        await ASGIApp(server)({"type": "bogus"}, _Receiver(), _Sender())
