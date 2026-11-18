import pytest

from mini_aiohttp import ClientError, ClientSession, CookieJar, Response, Server


@pytest.fixture
async def server():
    srv = Server()
    state = {"flaky_calls": 0, "post_calls": 0}

    @srv.route("/start")
    async def start(req):
        return Response(302, "", {"Location": "/end"})

    @srv.route("/end")
    async def end(req):
        return Response(200, "end")

    @srv.route("/loop")
    async def loop(req):
        return Response(302, "", {"Location": "/loop"})

    @srv.route("/see", method="POST")
    async def see(req):
        return Response(303, "", {"Location": "/whoami"})

    @srv.route("/whoami", method="GET")
    async def whoami(req):
        return Response(200, "GET")

    @srv.route("/whoami", method="POST")
    async def whoami_post(req):
        return Response(200, "POST")

    @srv.route("/flaky")
    async def flaky(req):
        state["flaky_calls"] += 1
        if state["flaky_calls"] < 3:
            return Response(503, "busy", {"Retry-After": "0"})
        return Response(200, "ok")

    @srv.route("/always503", method="POST")
    async def always503(req):
        state["post_calls"] += 1
        return Response(503, "busy", {"Retry-After": "0"})

    @srv.route("/set")
    async def set_cookie(req):
        return Response(200, "set", {"Set-Cookie": "sid=abc; Path=/"})

    @srv.route("/set-secure")
    async def set_secure(req):
        return Response(200, "set", {"Set-Cookie": "sid=sec; Path=/; Secure"})

    @srv.route("/check")
    async def check(req):
        return Response(200, req.headers.get("cookie", "none"))

    @srv.route("/auth")
    async def auth(req):
        return Response(200, req.headers.get("authorization", "none"))

    @srv.route("/stream")
    async def stream_route(req):
        return Response(200, b"x" * 100_000)

    @srv.route("/whoami-headers")
    async def whoami_headers(req):
        return Response(200, req.headers.get("x-test", "none"))

    srv.state = state
    await srv.start("127.0.0.1", 0)
    yield srv
    await srv.close()


def url(srv, path):
    return f"http://127.0.0.1:{srv.port}{path}"


# ---------- redirects ----------

async def test_follows_redirect(server):
    async with ClientSession() as s:
        r = await s.get(url(server, "/start"))
    assert r.status == 200
    assert r.body == b"end"


async def test_redirect_loop_is_limited(server):
    async with ClientSession(max_redirects=3) as s:
        with pytest.raises(ClientError, match="Too many redirects"):
            await s.get(url(server, "/loop"))


async def test_303_turns_post_into_get(server):
    async with ClientSession() as s:
        r = await s.post(url(server, "/see"), data="x")
    assert r.status == 200
    assert r.body == b"GET"


async def test_allow_redirects_false_returns_redirect(server):
    async with ClientSession() as s:
        r = await s.get(url(server, "/start"), allow_redirects=False)
    assert r.status == 302
    assert r.headers["location"] == "/end"


async def test_authorization_not_sent_to_other_origin(server):
    other = Server()

    @other.route("/landing")
    async def landing(req):
        return Response(200, req.headers.get("authorization", "none"))

    await other.start("127.0.0.1", 0)
    try:
        @server.route("/jump")
        async def jump(req):
            return Response(302, "", {"Location": f"http://127.0.0.1:{other.port}/landing"})

        async with ClientSession() as s:
            r = await s.get(url(server, "/jump"), headers={"Authorization": "Bearer secret"})
        assert r.body == b"none"
    finally:
        await other.close()


# ---------- retries ----------

async def test_get_is_retried_on_503(server):
    async with ClientSession(retries=3, backoff=0.01) as s:
        r = await s.get(url(server, "/flaky"))
    assert r.status == 200
    assert server.state["flaky_calls"] == 3


async def test_post_is_never_retried_on_503(server):
    async with ClientSession(retries=3, backoff=0.01) as s:
        r = await s.post(url(server, "/always503"), data="x")
    assert r.status == 503
    assert server.state["post_calls"] == 1


async def test_connection_error_retried_for_get_then_raised():
    attempts = []

    async def hook(method, u, headers):
        attempts.append(u)

    async with ClientSession(retries=2, backoff=0.01, on_request=[hook]) as s:
        with pytest.raises(OSError):
            await s.get("http://127.0.0.1:1/")
    assert len(attempts) == 3  # first try plus two retries


async def test_connection_error_not_retried_for_post():
    attempts = []

    async def hook(method, u, headers):
        attempts.append(method)

    async with ClientSession(retries=2, backoff=0.01, on_request=[hook]) as s:
        with pytest.raises(OSError):
            await s.post("http://127.0.0.1:1/", data="x")
    assert attempts == ["POST"]


def test_retry_after_is_capped_and_backoff_grows():
    s = ClientSession(backoff=0.5)
    assert s._backoff_delay(0, "2") == 2.0
    assert s._backoff_delay(0, "100000") == 60.0
    assert s._backoff_delay(3, None) == 4.0
    assert s._backoff_delay(20, None) == 10.0


# ---------- cookies ----------

async def test_cookie_is_stored_and_sent_back(server):
    async with ClientSession() as s:
        await s.get(url(server, "/set"))
        r = await s.get(url(server, "/check"))
    assert r.body == b"sid=abc"


async def test_secure_cookie_not_sent_over_http(server):
    async with ClientSession() as s:
        await s.get(url(server, "/set-secure"))
        r = await s.get(url(server, "/check"))
    assert r.body == b"none"


async def test_cookies_can_be_disabled(server):
    async with ClientSession(cookies=False) as s:
        await s.get(url(server, "/set"))
        r = await s.get(url(server, "/check"))
    assert r.body == b"none"


def test_cookie_with_foreign_domain_is_rejected():
    jar = CookieJar()
    jar.update("http://127.0.0.1/", ["evil=1; Domain=evil.com"])
    assert jar.header_for("http://127.0.0.1/") is None


def test_cookie_max_age_zero_deletes():
    jar = CookieJar()
    jar.update("http://example.com/", ["a=1; Path=/"])
    assert jar.header_for("http://example.com/") == "a=1"
    jar.update("http://example.com/", ["a=; Path=/; Max-Age=0"])
    assert jar.header_for("http://example.com/") is None


def test_cookie_path_matching():
    jar = CookieJar()
    jar.update("http://example.com/app/x", ["k=v; Path=/app"])
    assert jar.header_for("http://example.com/app/page") == "k=v"
    assert jar.header_for("http://example.com/other") is None


def test_cookie_rejects_crlf_in_value():
    jar = CookieJar()
    jar.update("http://example.com/", ["a=1\r\nX-Evil: 1"])
    assert jar.header_for("http://example.com/") is None


# ---------- streaming ----------

async def test_streaming_download_matches_full_body(server):
    async with ClientSession() as s:
        r = await s.get(url(server, "/stream"), stream=True)
        chunks = [c async for c in r.iter_chunks()]
    assert b"".join(chunks) == b"x" * 100_000


async def test_aread_on_streamed_response(server):
    async with ClientSession() as s:
        r = await s.get(url(server, "/stream"), stream=True)
        body = await r.aread()
    assert body == b"x" * 100_000


async def test_streamed_connection_is_reused_after_full_read(server):
    async with ClientSession() as s:
        r = await s.get(url(server, "/stream"), stream=True)
        await r.aread()
        key = (False, "127.0.0.1", server.port)
        assert len(s._idle[key]) == 1


async def test_partial_stream_closes_connection(server):
    async with ClientSession() as s:
        r = await s.get(url(server, "/stream"), stream=True)
        async for _ in r.iter_chunks():
            break
        await r.aclose()
        key = (False, "127.0.0.1", server.port)
        assert len(s._idle[key]) == 0


async def test_max_response_size_applies_to_streams(server):
    async with ClientSession(max_response_size=1000) as s:
        with pytest.raises(ClientError):
            await s.get(url(server, "/stream"), stream=True)


# ---------- hooks, defaults ----------

async def test_hooks_see_requests_and_responses(server):
    seen_req, seen_resp = [], []

    def on_req(method, u, headers):
        seen_req.append((method, u))

    def on_resp(resp):
        seen_resp.append(resp.status)

    async with ClientSession(on_request=[on_req], on_response=[on_resp]) as s:
        await s.get(url(server, "/end"))
    assert seen_req == [("GET", url(server, "/end"))]
    assert seen_resp == [200]


async def test_async_hook_is_awaited(server):
    calls = []

    async def on_req(method, u, headers):
        calls.append(method)
        headers["X-Test"] = "from-hook"

    async with ClientSession(on_request=[on_req]) as s:
        r = await s.get(url(server, "/whoami-headers"))
    assert calls == ["GET"]
    assert r.body == b"from-hook"


def test_connect_timeout_and_ssl_context_are_configurable():
    import ssl

    ctx = ssl.create_default_context()
    s = ClientSession(connect_timeout=1.5, ssl_context=ctx)
    assert s.connect_timeout == 1.5
    assert s.ssl_context is ctx
