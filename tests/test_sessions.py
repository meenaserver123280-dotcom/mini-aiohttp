"""Signed-cookie session middleware."""
from __future__ import annotations

import base64
import json

import pytest

from mini_aiohttp import Request, Response
from mini_aiohttp.sessions import SessionMiddleware

SECRET = "x" * 40


class Clock:
    def __init__(self, now=1_700_000_000):
        self.now = now

    def __call__(self):
        return self.now


def _cookie_value(resp):
    raw = next(v for v in resp._out_set_cookies if v.startswith("session="))
    return raw.split(";", 1)[0].split("=", 1)[1]


def _request(cookie=None):
    headers = {"cookie": f"session={cookie}"} if cookie else {}
    return Request("GET", "/", "", "HTTP/1.1", headers, b"")


def _app(mw, handler):
    async def run(req):
        async def call_next(r):
            return await handler(r)

        return await mw(req, call_next)

    return run


async def test_set_then_read_round_trip():
    mw = SessionMiddleware(SECRET, secure=False)

    async def write(req):
        req.session["user_id"] = 42
        return Response(200, "ok")

    resp = await _app(mw, write)(_request())
    cookie = _cookie_value(resp)

    seen = {}

    async def read(req):
        seen["user_id"] = req.session.get("user_id")
        return Response(200, "ok")

    await _app(mw, read)(_request(cookie))
    assert seen["user_id"] == 42


async def test_cookie_flags_default_to_secure_httponly_samesite():
    mw = SessionMiddleware(SECRET)

    async def write(req):
        req.session["a"] = 1
        return Response(200, "ok")

    resp = await _app(mw, write)(_request())
    raw = next(v for v in resp._out_set_cookies if v.startswith("session="))
    assert "Secure" in raw
    assert "HttpOnly" in raw
    assert "SameSite=Lax" in raw
    assert "Max-Age=3600" in raw


async def test_tampered_payload_is_rejected():
    mw = SessionMiddleware(SECRET, secure=False)
    token = mw.encode({"role": "user"})
    payload, sig = token.split(".")
    forged_body = base64.urlsafe_b64encode(
        json.dumps({"d": {"role": "admin"}, "exp": 9_999_999_999}).encode()
    ).rstrip(b"=").decode()
    assert mw.decode(f"{forged_body}.{sig}") == {}


async def test_wrong_secret_is_rejected():
    token = SessionMiddleware(SECRET, secure=False).encode({"a": 1})
    other = SessionMiddleware("y" * 40, secure=False)
    assert other.decode(token) == {}


async def test_expired_session_is_rejected():
    clock = Clock()
    mw = SessionMiddleware(SECRET, secure=False, max_age=60, clock=clock)
    token = mw.encode({"a": 1})
    clock.now += 61
    assert mw.decode(token) == {}


async def test_malformed_cookies_are_ignored():
    mw = SessionMiddleware(SECRET, secure=False)
    for junk in ["", "nodot", ".", "a.b", "!!!.???", "abc.def.ghi"]:
        assert mw.decode(junk) == {}


async def test_missing_cookie_gives_empty_session():
    mw = SessionMiddleware(SECRET, secure=False)
    seen = {}

    async def read(req):
        seen["data"] = req.session.to_dict()
        return Response(200, "ok")

    await _app(mw, read)(_request())
    assert seen["data"] == {}


async def test_unmodified_session_sets_no_cookie():
    mw = SessionMiddleware(SECRET, secure=False)

    async def read(req):
        _ = req.session.get("a")
        return Response(200, "ok")

    resp = await _app(mw, read)(_request())
    assert resp._out_set_cookies == []


async def test_clear_expires_the_cookie():
    mw = SessionMiddleware(SECRET, secure=False)
    cookie = mw.encode({"a": 1})

    async def logout(req):
        req.session.clear()
        return Response(200, "bye")

    resp = await _app(mw, logout)(_request(cookie))
    raw = next(v for v in resp._out_set_cookies if v.startswith("session="))
    assert "Max-Age=0" in raw
    assert raw.startswith("session=;")


async def test_popping_every_key_expires_the_cookie():
    mw = SessionMiddleware(SECRET, secure=False)
    cookie = mw.encode({"a": 1})

    async def pop_all(req):
        req.session.pop("a")
        return Response(200, "ok")

    resp = await _app(mw, pop_all)(_request(cookie))
    raw = next(v for v in resp._out_set_cookies if v.startswith("session="))
    assert "Max-Age=0" in raw


async def test_oversized_session_raises():
    mw = SessionMiddleware(SECRET, secure=False)

    async def big(req):
        req.session["blob"] = "z" * 5000
        return Response(200, "ok")

    with pytest.raises(ValueError):
        await _app(mw, big)(_request())


def test_secret_must_be_long_enough():
    with pytest.raises(ValueError):
        SessionMiddleware("short")


def test_max_age_must_be_positive():
    with pytest.raises(ValueError):
        SessionMiddleware(SECRET, max_age=0)
