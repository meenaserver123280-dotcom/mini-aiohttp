"""Tests for CSRF protection, basic auth and API key auth. No network access."""
from __future__ import annotations

import base64
from http.cookies import SimpleCookie

import pytest

from mini_aiohttp import Request, Response, api_key_auth, basic_auth, csrf_protect

SECRET = "s" * 32


def make_request(method="GET", headers=None, body=b""):
    req = Request(method, "/x", "", "HTTP/1.1", headers or {}, body)
    req.client = "203.0.113.9"
    return req


async def run(middleware, req):
    async def call_next(_req):
        return Response(200, "ok")
    return await middleware(req, call_next)


def cookie_value(resp, name):
    jar = SimpleCookie()
    jar.load(resp._out_set_cookies[0])
    return jar[name].value


# ---------- CSRF ----------

async def test_get_issues_token_cookie_and_exposes_it_to_handler():
    seen = {}

    async def call_next(req):
        seen["token"] = req.csrf_token
        return Response(200, "ok")

    mw = csrf_protect(SECRET)
    resp = await mw(make_request("GET"), call_next)
    assert resp.status == 200
    assert cookie_value(resp, "csrf_token") == seen["token"]


async def test_post_without_token_is_forbidden():
    mw = csrf_protect(SECRET)
    assert (await run(mw, make_request("POST"))).status == 403


async def test_post_with_matching_header_passes():
    mw = csrf_protect(SECRET)
    token = cookie_value(await run(mw, make_request("GET")), "csrf_token")
    req = make_request("POST", {"cookie": f"csrf_token={token}", "x-csrf-token": token})
    assert (await run(mw, req)).status == 200


async def test_post_with_matching_form_field_passes():
    mw = csrf_protect(SECRET)
    token = cookie_value(await run(mw, make_request("GET")), "csrf_token")
    body = f"csrf_token={token}&name=ada".encode()
    headers = {
        "cookie": f"csrf_token={token}",
        "content-type": "application/x-www-form-urlencoded",
    }
    assert (await run(mw, make_request("POST", headers, body))).status == 200


async def test_mismatched_token_is_forbidden():
    mw = csrf_protect(SECRET)
    token = cookie_value(await run(mw, make_request("GET")), "csrf_token")
    other = cookie_value(await run(mw, make_request("GET")), "csrf_token")
    req = make_request("POST", {"cookie": f"csrf_token={token}", "x-csrf-token": other})
    assert (await run(mw, req)).status == 403


async def test_forged_cookie_is_forbidden():
    mw = csrf_protect(SECRET)
    req = make_request("POST", {"cookie": "csrf_token=abc.def", "x-csrf-token": "abc.def"})
    assert (await run(mw, req)).status == 403


async def test_safe_methods_do_not_need_a_token():
    mw = csrf_protect(SECRET)
    assert (await run(mw, make_request("HEAD"))).status == 200


def test_csrf_secret_must_be_long_enough():
    with pytest.raises(ValueError):
        csrf_protect("short")


# ---------- basic auth ----------

def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


async def test_basic_auth_accepts_valid_credentials_and_sets_user():
    mw = basic_auth({"admin": "s3cret"})
    req = make_request(headers={"authorization": basic("admin", "s3cret")})
    seen = {}

    async def call_next(r):
        seen["user"] = r.user
        return Response(200, "ok")

    assert (await mw(req, call_next)).status == 200
    assert seen["user"] == "admin"


@pytest.mark.parametrize("header", [
    None,
    "Basic !!!not-base64",
    basic("admin", "wrong"),
    basic("nobody", "s3cret"),
    "Bearer token",
])
async def test_basic_auth_rejects_bad_credentials(header):
    mw = basic_auth({"admin": "s3cret"}, realm="Admin area")
    headers = {} if header is None else {"authorization": header}
    resp = await run(mw, make_request(headers=headers))
    assert resp.status == 401
    assert resp.headers["www-authenticate"] == 'Basic realm="Admin area", charset="UTF-8"'


async def test_basic_auth_verify_callable():
    mw = basic_auth(verify=lambda user, pw: user == "ada" and pw == "x")
    assert (await run(mw, make_request(headers={"authorization": basic("ada", "x")}))).status == 200
    assert (await run(mw, make_request(headers={"authorization": basic("ada", "y")}))).status == 401


def test_basic_auth_needs_exactly_one_source():
    with pytest.raises(ValueError):
        basic_auth()
    with pytest.raises(ValueError):
        basic_auth({}, verify=lambda u, p: True)


# ---------- API keys ----------

async def test_api_key_accepts_any_configured_key():
    mw = api_key_auth(["key-one", "key-two"])
    assert (await run(mw, make_request(headers={"x-api-key": "key-two"}))).status == 200
    assert (await run(mw, make_request(headers={"x-api-key": "key-one"}))).status == 200


@pytest.mark.parametrize("headers", [{}, {"x-api-key": "nope"}, {"x-api-key": ""}])
async def test_api_key_rejects_missing_or_wrong_key(headers):
    mw = api_key_auth(["key-one"])
    assert (await run(mw, make_request(headers=headers))).status == 401


async def test_api_key_custom_header_and_verify():
    mw = api_key_auth(verify=lambda key: key == "abc", header="X-Token")
    assert (await run(mw, make_request(headers={"x-token": "abc"}))).status == 200
    assert (await run(mw, make_request(headers={"x-api-key": "abc"}))).status == 401


def test_api_key_needs_keys_or_verify():
    with pytest.raises(ValueError):
        api_key_auth()
    with pytest.raises(ValueError):
        api_key_auth([""])


def test_401_has_a_reason_phrase():
    assert Response(401, "x").reason == "Unauthorized"
