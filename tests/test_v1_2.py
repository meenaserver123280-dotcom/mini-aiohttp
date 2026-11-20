"""Tests for route limits, background tasks and shutdown, Telegram webhooks and database adapters.

No network access: sessions are fakes, and the server test binds to 127.0.0.1 on port 0.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from mini_aiohttp import (
    BackgroundTasks,
    FirebaseRTDB,
    RateLimiter,
    Request,
    Response,
    Server,
    TaskQueue,
    TelegramError,
    TelegramWebhook,
    route_limit,
    route_middleware,
    serve,
    update_message,
)
from mini_aiohttp.db import FirebaseError, _driver
from mini_aiohttp.webhook import TELEGRAM_SECRET_HEADER


def make_request(method="GET", path="/x", headers=None, body=b"", client="10.0.0.1"):
    req = Request(method, path, "", "HTTP/1.1", headers or {}, body)
    req.client = client
    return req


# ---------- route-level middleware ----------

async def test_route_limit_applies_only_to_its_handler():
    limiter = RateLimiter(rate=0.001, burst=2)

    @route_limit(limiter)
    async def limited(req):
        return "ok"

    async def free(req):
        return "free"

    req = make_request()
    assert (await limited(req)).status == 200
    assert (await limited(req)).status == 200
    assert (await limited(req)).status == 429
    assert await free(req) == "free"
    assert await free(req) == "free"


async def test_route_middleware_runs_in_listed_order():
    calls = []

    def make(tag):
        async def mw(req, call_next):
            calls.append(f"{tag}-in")
            resp = await call_next(req)
            calls.append(f"{tag}-out")
            return resp
        return mw

    @route_middleware(make("a"), make("b"))
    async def handler(req):
        calls.append("handler")
        return Response(200, "x")

    await handler(make_request())
    assert calls == ["a-in", "b-in", "handler", "b-out", "a-out"]


# ---------- background tasks, queue and shutdown ----------

async def test_background_tasks_finish_or_get_cancelled_on_close():
    bg = BackgroundTasks()
    done = []

    async def quick():
        await asyncio.sleep(0)
        done.append("quick")

    async def slow():
        await asyncio.sleep(60)

    bg.spawn(quick())
    slow_task = bg.spawn(slow())
    await bg.close(grace=0.05)
    assert done == ["quick"]
    assert slow_task.cancelled()
    with pytest.raises(RuntimeError):
        bg.spawn(quick())


async def test_task_queue_handles_jobs_and_survives_errors():
    seen = []

    async def handler(job):
        if job == "bad":
            raise ValueError("boom")
        seen.append(job)

    bg = BackgroundTasks()
    queue = TaskQueue(handler, workers=2, tasks=bg)
    for job in ["a", "bad", "b"]:
        await queue.put(job)
    await queue.join()
    await queue.close(grace=1)
    await bg.close(grace=1)
    assert sorted(seen) == ["a", "b"]
    with pytest.raises(RuntimeError):
        await queue.put("late")


async def test_serve_stops_on_event_and_runs_hooks_in_order():
    server = Server()
    stop = asyncio.Event()
    order = []
    bg = BackgroundTasks()

    async def hook_a():
        order.append("a")

    def hook_b():
        order.append("b")

    async def forever():
        await asyncio.sleep(60)

    bg.spawn(forever())
    task = asyncio.ensure_future(
        serve(server, "127.0.0.1", 0, grace=0.2, background=bg,
              shutdown=[hook_a, hook_b], stop_event=stop, signals=())
    )
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert order == ["a", "b"]
    assert len(bg) == 0
    assert bg.closed


# ---------- Telegram webhook ----------

class _FakeResponse:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    async def aclose(self):
        pass


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


async def _noop(update):
    return None


def test_telegram_webhook_validates_arguments():
    with pytest.raises(ValueError):
        TelegramWebhook(None, "1:A", _noop, secret_token="bad secret!")
    with pytest.raises(ValueError):
        TelegramWebhook(None, "", _noop, secret_token="ok_secret")


async def test_set_webhook_sends_url_and_secret():
    session = _FakeSession([_FakeResponse(200, {"ok": True, "result": True})])
    bot = TelegramWebhook(session, "123:ABC", _noop, secret_token="s3cret")
    assert await bot.set_webhook("https://example.com/telegram", allowed_updates=["message"]) is True
    _, url, kwargs = session.calls[0]
    assert url.endswith("/bot123:ABC/setWebhook")
    assert kwargs["json"]["secret_token"] == "s3cret"
    assert kwargs["json"]["allowed_updates"] == ["message"]


async def test_set_webhook_requires_https():
    bot = TelegramWebhook(_FakeSession([]), "1:A", _noop, secret_token="s3cret")
    with pytest.raises(ValueError):
        await bot.set_webhook("http://example.com/telegram")


async def test_api_error_carries_telegram_description():
    session = _FakeSession([_FakeResponse(400, {"ok": False, "description": "Bad Request: bad url"})])
    bot = TelegramWebhook(session, "1:A", _noop, secret_token="s3cret")
    with pytest.raises(TelegramError) as info:
        await bot.delete_webhook()
    assert info.value.description == "Bad Request: bad url"


async def test_webhook_checks_secret_and_dispatches_update():
    got = []

    async def handler(update):
        got.append(update["update_id"])

    bot = TelegramWebhook(_FakeSession([]), "1:A", handler, secret_token="s3cret")
    body = json.dumps({"update_id": 7, "message": {"text": "hi"}}).encode()
    wrong = make_request("POST", "/telegram", {TELEGRAM_SECRET_HEADER: "nope"}, body)
    assert (await bot.handle(wrong)).status == 403
    right = make_request("POST", "/telegram", {TELEGRAM_SECRET_HEADER: "s3cret"}, body)
    assert (await bot.handle(right)).status == 200
    await bot.close()
    assert got == [7]


def test_update_message_picks_message_kinds():
    assert update_message({"message": {"text": "a"}}) == {"text": "a"}
    assert update_message({"channel_post": {"text": "b"}}) == {"text": "b"}
    assert update_message({"callback_query": {}}) is None


# ---------- database adapters ----------

def test_missing_driver_error_names_the_extra():
    with pytest.raises(ImportError, match=r"mini-aiohttp\[no_such_extra\]"):
        _driver("definitely_not_a_real_module_xyz", "no_such_extra")


async def test_firebase_builds_urls_and_reads_json():
    session = _FakeSession([_FakeResponse(200, {"name": "Ada"})])
    db = FirebaseRTDB(session, "https://demo.firebaseio.com/", auth_token="tok")
    assert await db.get("users/42") == {"name": "Ada"}
    method, url, _ = session.calls[0]
    assert method == "GET"
    assert url == "https://demo.firebaseio.com/users/42.json?auth=tok"


async def test_firebase_error_hides_token():
    session = _FakeSession([_FakeResponse(401, None)])
    db = FirebaseRTDB(session, "https://demo.firebaseio.com", auth_token="secret-token")
    with pytest.raises(FirebaseError) as info:
        await db.get("x")
    assert info.value.status == 401
    assert "secret-token" not in str(info.value)


def test_firebase_requires_https():
    with pytest.raises(ValueError):
        FirebaseRTDB(_FakeSession([]), "http://demo.firebaseio.com")
