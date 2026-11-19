"""Tests for the 0.10 helpers: retry policy, proxy and header helpers, webhook, polling, traffic recorder, resp.json()."""
from __future__ import annotations

import asyncio
import random

import pytest

from mini_aiohttp import (
    ClientError,
    HeaderRotator,
    Request,
    Response,
    RetryPolicy,
    TelegramError,
    TelegramPoller,
    TrafficRecorderMiddleware,
    WebhookRouter,
    merge_headers,
    socks5_url,
    tor_proxy,
)


# ---------- resp.json() on responses, and Response.json(data) still builds ----------

def test_response_json_parses_body_and_class_builds():
    parsed = Response(200, b'{"ok": true, "n": 3}').json()
    assert parsed == {"ok": True, "n": 3}
    built = Response.json({"x": 1}, status=201)
    assert built.status == 201
    assert built.headers["content-type"] == "application/json; charset=utf-8"
    assert built.json() == {"x": 1}


async def test_response_json_refuses_unread_stream():
    async def chunks():
        yield b"{}"

    streamed = Response(200, b"")
    streamed._chunks = chunks()
    with pytest.raises(RuntimeError):
        streamed.json()
    await streamed.aclose()


# ---------- RetryPolicy ----------

class FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def request(self, method, url, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def fast_policy(**kwargs):
    options = dict(max_retries=3, base_delay=0.0, max_delay=0.0, rng=random.Random(1))
    options.update(kwargs)
    return RetryPolicy(**options)


async def test_get_retries_503_until_success():
    session = FakeSession([Response(503), Response(503), Response(200, b"ok")])
    response = await fast_policy().send(session, "GET", "https://example.com/")
    assert response.status == 200
    assert session.calls == 3


async def test_post_is_not_retried_on_503_but_is_on_429():
    session = FakeSession([Response(503), Response(200)])
    response = await fast_policy().send(session, "POST", "https://example.com/")
    assert response.status == 503 and session.calls == 1

    session = FakeSession([Response(429), Response(200, b"ok")])
    response = await fast_policy().send(session, "POST", "https://example.com/")
    assert response.status == 200 and session.calls == 2


async def test_returns_last_response_when_retries_run_out():
    session = FakeSession([Response(502)] * 4)
    response = await fast_policy(max_retries=3).send(session, "GET", "https://example.com/")
    assert response.status == 502
    assert session.calls == 4


async def test_connection_errors_are_retried_for_get():
    session = FakeSession([ConnectionError("reset"), Response(200, b"ok")])
    response = await fast_policy(max_retries=2).send(session, "GET", "https://example.com/")
    assert response.status == 200 and session.calls == 2


async def test_connection_error_is_raised_when_budget_is_spent():
    session = FakeSession([ConnectionError("reset")] * 2)
    with pytest.raises(ConnectionError):
        await fast_policy(max_retries=1).send(session, "GET", "https://example.com/")
    assert session.calls == 2


async def test_post_connection_error_is_not_retried():
    session = FakeSession([ClientError("boom"), Response(200)])
    with pytest.raises(ClientError):
        await fast_policy().send(session, "POST", "https://example.com/")
    assert session.calls == 1


async def test_async_iterable_body_is_never_replayed():
    async def body():
        yield b"chunk"

    session = FakeSession([Response(503), Response(200)])
    response = await fast_policy().send(session, "PUT", "https://example.com/", data=body())
    assert response.status == 503 and session.calls == 1


def test_retry_after_is_honoured_and_capped():
    policy = RetryPolicy(retry_after_cap=5.0, jitter=False)
    assert policy.delay(0, retry_after="2") == 2.0
    assert policy.delay(0, retry_after="600") == 5.0
    # HTTP-date values fall back to backoff.
    assert policy.delay(1, retry_after="Wed, 21 Oct 2026 07:28:00 GMT") == policy.base_delay * 2


def test_backoff_is_jittered_within_ceiling():
    policy = RetryPolicy(base_delay=1.0, max_delay=4.0, rng=random.Random(7))
    for attempt in range(6):
        ceiling = min(4.0, 1.0 * 2 ** attempt)
        assert 0.0 <= policy.delay(attempt) <= ceiling


def test_retry_policy_validates_arguments():
    with pytest.raises(ValueError):
        RetryPolicy(max_retries=-1)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=2.0, max_delay=1.0)


# ---------- proxy and headers ----------

def test_tor_and_socks5_urls():
    assert tor_proxy() == "socks5h://127.0.0.1:9050"
    assert socks5_url("10.0.0.2", 1080, remote_dns=False) == "socks5://10.0.0.2:1080"
    assert socks5_url("h", 1080, username="u@x", password="p:/w") == "socks5h://u%40x:p%3A%2Fw@h:1080"
    with pytest.raises(ValueError):
        socks5_url("h", 0)
    with pytest.raises(ValueError):
        socks5_url("", 1080)


def test_merge_headers_is_case_insensitive_and_last_wins():
    merged = merge_headers({"User-Agent": "a", "Accept": "x"}, {"user-agent": "b"}, None)
    assert merged == {"Accept": "x", "user-agent": "b"}


def test_merge_headers_rejects_injection():
    with pytest.raises(ValueError):
        merge_headers({"X-Test": "ok\r\nEvil: 1"})


def test_header_rotator_round_robin_returns_copies():
    rotator = HeaderRotator([{"User-Agent": "a"}, {"User-Agent": "b"}])
    first = rotator.next()
    first["User-Agent"] = "changed"
    assert [rotator.next()["User-Agent"] for _ in range(3)] == ["b", "a", "b"]


def test_header_rotator_needs_presets():
    with pytest.raises(ValueError):
        HeaderRotator([])


# ---------- webhook router ----------

def make_request(body: bytes, headers=None):
    return Request("POST", "/webhook", "", "HTTP/1.1", headers or {}, body)


async def test_webhook_rejects_wrong_secret_and_accepts_right_one():
    seen = []

    async def handler(update):
        seen.append(update["update_id"])

    router = WebhookRouter(handler, secret_token="s3cret")
    bad = await router.handle(make_request(b'{"update_id": 1}', {"x-telegram-bot-api-secret-token": "nope"}))
    missing = await router.handle(make_request(b'{"update_id": 1}'))
    good = await router.handle(make_request(b'{"update_id": 1}', {"x-telegram-bot-api-secret-token": "s3cret"}))
    await router.close()
    assert (bad.status, missing.status, good.status) == (403, 403, 200)
    assert seen == [1]


async def test_webhook_deduplicates_update_ids_and_acks_fast():
    seen = []

    async def handler(update):
        await asyncio.sleep(0.05)
        seen.append(update["update_id"])

    router = WebhookRouter(handler)
    first = await router.handle(make_request(b'{"update_id": 7, "message": {}}'))
    again = await router.handle(make_request(b'{"update_id": 7, "message": {}}'))
    assert first.status == 200 and again.json() == {"ok": True, "duplicate": True}
    await router.close()
    assert seen == [7]


async def test_webhook_dedupe_expires_after_ttl():
    now = [0.0]
    seen = []

    async def handler(update):
        seen.append(update["update_id"])

    router = WebhookRouter(handler, dedupe_ttl=10.0, clock=lambda: now[0])
    await router.handle(make_request(b'{"update_id": 5}'))
    now[0] = 11.0
    await router.handle(make_request(b'{"update_id": 5}'))
    await router.close()
    assert seen == [5, 5]


async def test_webhook_bad_bodies_get_400():
    router = WebhookRouter(lambda u: None)
    assert (await router.handle(make_request(b"not json"))).status == 400
    assert (await router.handle(make_request(b"[1, 2]"))).status == 400
    await router.close()


async def test_webhook_handler_errors_do_not_escape():
    async def handler(update):
        raise RuntimeError("bot bug")

    router = WebhookRouter(handler)
    response = await router.handle(make_request(b'{"update_id": 9}'))
    await router.close()
    assert response.status == 200


def test_webhook_validates_arguments():
    with pytest.raises(ValueError):
        WebhookRouter(lambda u: None, secret_token="")
    with pytest.raises(ValueError):
        WebhookRouter(lambda u: None, dedupe_size=0)


# ---------- telegram polling ----------

class PollSession:
    def __init__(self, replies):
        self.replies = list(replies)
        self.payloads = []

    async def request(self, method, url, **kwargs):
        self.payloads.append(kwargs.get("json"))
        return self.replies.pop(0)


async def test_poller_tracks_offset_and_dispatches():
    session = PollSession([
        Response.json({"ok": True, "result": [{"update_id": 10}, {"update_id": 11}]}),
        Response.json({"ok": True, "result": []}),
    ])
    handled = []

    async def handler(update):
        handled.append(update["update_id"])

    poller = TelegramPoller(session, "123:ABC", handler, timeout=0)
    assert await poller.run_once() == 2
    assert poller.offset == 12
    assert await poller.run_once() == 0
    assert handled == [10, 11]
    assert session.payloads[1]["offset"] == 12


async def test_poller_raises_telegram_error_on_not_ok():
    session = PollSession([Response.json({"ok": False, "description": "Unauthorized"}, status=200)])
    poller = TelegramPoller(session, "123:ABC", lambda u: None, timeout=0)
    with pytest.raises(TelegramError) as info:
        await poller.get_updates()
    assert info.value.description == "Unauthorized"


def test_poller_requires_token():
    with pytest.raises(ValueError):
        TelegramPoller(PollSession([]), "", lambda u: None)


# ---------- traffic recorder ----------

async def test_traffic_recorder_keeps_recent_requests():
    times = iter([0.0, 0.25])
    recorder = TrafficRecorderMiddleware(maxlen=2, body_preview=5, clock=lambda: next(times))

    async def call_next(req):
        return Response(201, b"made")

    req = Request("POST", "/items", "", "HTTP/1.1", {}, b"payload-that-is-long", client="10.0.0.9")
    response = await recorder(req, call_next)
    assert response.status == 201
    (entry,) = recorder.records
    assert entry["method"] == "POST" and entry["path"] == "/items"
    assert entry["status"] == 201 and entry["client"] == "10.0.0.9"
    assert entry["duration_ms"] == 250.0
    assert entry["body_preview"] == "paylo"


async def test_traffic_recorder_records_500_when_handler_raises():
    recorder = TrafficRecorderMiddleware()

    async def call_next(req):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await recorder(Request("GET", "/x", "", "HTTP/1.1"), call_next)
    assert recorder.records[0]["status"] == 500


async def test_traffic_recorder_bounded_and_body_preview_off_by_default():
    recorder = TrafficRecorderMiddleware(maxlen=2)

    async def call_next(req):
        return Response(200)

    for path in ("/a", "/b", "/c"):
        await recorder(Request("GET", path, "", "HTTP/1.1", {}, b"secret"), call_next)
    assert [r["path"] for r in recorder.records] == ["/b", "/c"]
    assert all(r["body_preview"] == "" for r in recorder.records)
    recorder.clear()
    assert recorder.records == []


def test_traffic_recorder_validates_arguments():
    with pytest.raises(ValueError):
        TrafficRecorderMiddleware(maxlen=0)
