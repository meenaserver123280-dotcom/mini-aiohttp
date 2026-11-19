"""Webhook receiver: secret token check, update deduplication and background dispatch.

    async def on_update(update: dict) -> None:
        ...

    router = WebhookRouter(on_update, secret_token=os.environ["WEBHOOK_SECRET"])
    router.attach(app, "/webhook")
    ...
    await router.close()          # on shutdown: wait for running handlers

Each request is answered at once with 200 {"ok": true}. The handler runs in a background
task. A request whose update_id was already seen (within `dedupe_ttl` seconds) is answered
with 200 and not dispatched again. Telegram resends updates it did not get a 2xx for, so
this stops duplicate handling.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections import OrderedDict

from .response import Response

log = logging.getLogger("mini_aiohttp.webhook")

# Header Telegram sends when a secret_token is set with setWebhook.
TELEGRAM_SECRET_HEADER = "x-telegram-bot-api-secret-token"


class _SeenIds:
    """Bounded set of recent update ids. Entries expire after `ttl` seconds.

    When full, the oldest entry is dropped first (FIFO). This is not a strict LRU.
    """

    def __init__(self, maxsize: int, ttl: float, clock):
        self._items: OrderedDict = OrderedDict()  # update_id -> expiry time
        self._maxsize = maxsize
        self._ttl = ttl
        self._clock = clock

    def _expire(self, now: float) -> None:
        while self._items:
            _, expires = next(iter(self._items.items()))
            if expires > now:
                break
            self._items.popitem(last=False)

    def add_if_new(self, key) -> bool:
        """True if `key` was not seen recently. Records it either way."""
        now = self._clock()
        self._expire(now)
        if key in self._items:
            return False
        self._items[key] = now + self._ttl
        while len(self._items) > self._maxsize:
            self._items.popitem(last=False)
        return True

    def __len__(self) -> int:
        return len(self._items)


class WebhookRouter:
    def __init__(
        self,
        handler,
        *,
        secret_token: str | None = None,
        secret_header: str = TELEGRAM_SECRET_HEADER,
        dedupe_size: int = 10_000,
        dedupe_ttl: float = 3600.0,
        max_tasks: int = 1000,
        clock=time.monotonic,
    ):
        if dedupe_size < 1:
            raise ValueError("dedupe_size must be at least 1")
        if dedupe_ttl <= 0:
            raise ValueError("dedupe_ttl must be positive")
        if max_tasks < 1:
            raise ValueError("max_tasks must be at least 1")
        if secret_token is not None and not secret_token:
            raise ValueError("secret_token must not be empty")
        self._handler = handler
        self._secret = secret_token.encode("utf-8") if secret_token else None
        self._secret_header = secret_header.lower()
        self._seen = _SeenIds(dedupe_size, dedupe_ttl, clock)
        self._max_tasks = max_tasks
        self._tasks: set = set()
        self._closed = False

    def _authorized(self, req) -> bool:
        if self._secret is None:
            return True
        given = req.headers.get(self._secret_header, "").encode("utf-8")
        return hmac.compare_digest(given, self._secret)

    async def handle(self, req) -> Response:
        if self._closed:
            return Response(503, "shutting down")
        if not self._authorized(req):
            return Response(403, "forbidden")
        try:
            update = req.json()
        except ValueError:
            return Response(400, "invalid json")
        if not isinstance(update, dict):
            return Response(400, "update must be a JSON object")

        update_id = update.get("update_id")
        is_id = isinstance(update_id, int) and not isinstance(update_id, bool)
        if len(self._tasks) >= self._max_tasks:
            # Do not record the id: Telegram retries later and the update is still handled.
            return Response(503, "busy")
        if is_id and not self._seen.add_if_new(update_id):
            return Response.json({"ok": True, "duplicate": True})

        task = asyncio.ensure_future(self._dispatch(update))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return Response.json({"ok": True})

    async def _dispatch(self, update: dict) -> None:
        try:
            await self._handler(update)
        except Exception:
            log.exception("webhook handler failed for update_id=%r", update.get("update_id"))

    def attach(self, app, path: str = "/webhook"):
        """Register the POST route on a Server. Returns the handler."""
        app.route(path, method="POST")(self.handle)
        return self.handle

    async def close(self, timeout: float = 10.0) -> None:
        """Stop accepting updates and wait up to `timeout` seconds for running handlers."""
        self._closed = True
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=timeout)
        for task in pending:
            task.cancel()
