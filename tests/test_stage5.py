import pytest

from mini_aiohttp import (
    ClientSession,
    RateLimiter,
    Response,
    Server,
    cors,
    ip_policy,
    require_json,
    security_headers,
    set_cookie,
)


async def start(srv):
    await srv.start("127.0.0.1", 0)
    return f"http://127.0.0.1:{srv.port}"


# ---------- security headers ----------

async def test_security_headers_on_success_and_404():
    srv = Server()
    srv.middleware_(security_headers(hsts_max_age=31536000))

    @srv.route("/")
    async def index(req):
        return Response(200, "ok")

    base = await start(srv)
    try:
        async with ClientSession() as s:
            ok = await s.get(base + "/")
            missing = await s.get(base + "/nope")
        for r in (ok, missing):
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["x-frame-options"] == "DENY"
            assert r.headers["content-security-policy"] == "default-src 'self'"
            assert "max-age=31536000" in r.headers["strict-transport-security"]
    finally:
        await srv.close()


# ---------- CORS ----------

async def test_cors_preflight_from_allowed_origin():
    srv = Server()
    srv.middleware_(cors(allow_origins=["https://app.example"]))

    @srv.route("/api", method="POST")
    async def api(req):
        return Response(200, "ok")

    base = await start(srv)
    try:
        async with ClientSession() as s:
            r = await s.request(
                "OPTIONS", base + "/api",
                headers={"Origin": "https://app.example", "Access-Control-Request-Method": "POST"},
            )
        assert r.status == 204
        assert r.headers["access-control-allow-origin"] == "https://app.example"
        assert "POST" in r.headers["access-control-allow-methods"]
    finally:
        await srv.close()


async def test_cors_unknown_origin_gets_no_allow_header():
    srv = Server()
    srv.middleware_(cors(allow_origins=["https://app.example"]))

    @srv.route("/api")
    async def api(req):
        return Response(200, "ok")

    base = await start(srv)
    try:
        async with ClientSession() as s:
            r = await s.get(base + "/api", headers={"Origin": "https://evil.example"})
        assert r.status == 200
        assert "access-control-allow-origin" not in r.headers
    finally:
        await srv.close()


def test_cors_wildcard_with_credentials_rejected():
    with pytest.raises(ValueError):
        cors(allow_origins=["*"], allow_credentials=True)


# ---------- rate limiting ----------

async def test_rate_limit_returns_429_with_retry_after():
    srv = Server()
    srv.middleware_(RateLimiter(rate=0.001, burst=2).middleware())

    @srv.route("/")
    async def index(req):
        return Response(200, "ok")

    base = await start(srv)
    try:
        async with ClientSession() as s:
            statuses = [(await s.get(base + "/")).status for _ in range(3)]
            last = await s.get(base + "/")
        assert statuses == [200, 200, 429]
        assert last.status == 429
        assert int(last.headers["retry-after"]) >= 1
    finally:
        await srv.close()


def test_rate_limiter_refills_over_time():
    now = [0.0]
    limiter = RateLimiter(rate=1.0, burst=1, clock=lambda: now[0])
    assert limiter.check("a") is None
    assert limiter.check("a") is not None
    now[0] = 1.5
    assert limiter.check("a") is None
    assert limiter.check("b") is None  # separate bucket per client


def test_rate_limiter_validates_arguments():
    with pytest.raises(ValueError):
        RateLimiter(rate=0, burst=1)


# ---------- IP policy ----------

async def test_ip_policy_deny_blocks_client():
    srv = Server()
    srv.middleware_(ip_policy(deny=["127.0.0.0/8"]))

    @srv.route("/")
    async def index(req):
        return Response(200, "ok")

    base = await start(srv)
    try:
        async with ClientSession() as s:
            r = await s.get(base + "/")
        assert r.status == 403
    finally:
        await srv.close()


async def test_ip_policy_allow_list():
    srv = Server()
    srv.middleware_(ip_policy(allow=["10.0.0.0/8"]))

    @srv.route("/")
    async def index(req):
        return Response(200, "ok")

    base = await start(srv)
    try:
        async with ClientSession() as s:
            blocked = await s.get(base + "/")
        assert blocked.status == 403  # 127.0.0.1 is not inside 10.0.0.0/8
    finally:
        await srv.close()

    srv2 = Server()
    srv2.middleware_(ip_policy(allow=["127.0.0.0/8"]))

    @srv2.route("/")
    async def index2(req):
        return Response(200, "ok")

    base2 = await start(srv2)
    try:
        async with ClientSession() as s:
            allowed = await s.get(base2 + "/")
        assert allowed.status == 200
    finally:
        await srv2.close()


# ---------- secure cookies ----------

async def test_set_cookie_has_safe_defaults():
    srv = Server()

    @srv.route("/login")
    async def login(req):
        return set_cookie(Response(200, "ok"), "sid", "a b", max_age=60)

    base = await start(srv)
    try:
        async with ClientSession(cookies=False) as s:
            r = await s.get(base + "/login")
        cookie = r.set_cookies[0]
        assert cookie.startswith("sid=a%20b")
        assert "Secure" in cookie and "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie and "Max-Age=60" in cookie
    finally:
        await srv.close()


def test_set_cookie_rejects_bad_input():
    resp = Response(200, "ok")
    with pytest.raises(ValueError):
        set_cookie(resp, "bad name", "x")
    with pytest.raises(ValueError):
        set_cookie(resp, "sid", "x", samesite="None", secure=False)
    with pytest.raises(ValueError):
        set_cookie(resp, "sid", "x", samesite="Whatever")


# ---------- JSON validation ----------

async def test_require_json_validates_fields():
    srv = Server()

    @srv.route("/users", method="POST")
    @require_json({"name": str, "age": int})
    async def create(req):
        return Response.json(req.json())

    base = await start(srv)
    try:
        async with ClientSession() as s:
            missing = await s.post(base + "/users", json={"name": "Asha"})
            wrong = await s.post(base + "/users", json={"name": "Asha", "age": "20"})
            boolean = await s.post(base + "/users", json={"name": "Asha", "age": True})
            not_object = await s.post(base + "/users", json=[1, 2])
            ok = await s.post(base + "/users", json={"name": "Asha", "age": 20})
        assert missing.status == 400
        assert wrong.status == 400
        assert boolean.status == 400
        assert not_object.status == 400
        assert ok.status == 200
    finally:
        await srv.close()
