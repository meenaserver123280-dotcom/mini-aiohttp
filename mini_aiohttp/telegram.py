"""Telegram Bot API: polling (TelegramPoller) or HTTPS webhook (TelegramWebhook).

Polling:

    async def on_update(update: dict) -> None:
        ...

    poller = TelegramPoller(session, token, on_update)
    await poller.run()          # runs until poller.stop() is called

Webhook:

    bot = TelegramWebhook(session, token, on_update, secret_token=os.environ["TG_SECRET"])
    await bot.set_webhook("https://example.com/telegram", allowed_updates=["message"])
    bot.attach(server, "/telegram")
    ...
    await bot.close()           # on shutdown: wait for running handlers

Use either polling or a webhook, not both: Telegram does not deliver updates through
getUpdates while a webhook is set.

The bot token is only put into the request URL. It is never logged. Errors are logged by
class name and Telegram's description, without the URL.
"""
from __future__ import annotations

import asyncio
import logging
import re

from .client import ClientError
from .retry import RetryPolicy
from .webhook import WebhookRouter

log = logging.getLogger("mini_aiohttp.telegram")

API_BASE = "https://api.telegram.org"
# Telegram accepts 1-256 characters from this set for secret_token.
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")


class TelegramError(Exception):
    """Telegram answered with ok=false. `description` is Telegram's message, without the token."""

    def __init__(self, description: str):
        super().__init__(description)
        self.description = description


def update_message(update: dict) -> dict | None:
    """The message inside an update (message, edited_message, channel_post or edited_channel_post)."""
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        value = update.get(key)
        if isinstance(value, dict):
            return value
    return None


class TelegramPoller:
    def __init__(
        self,
        session,
        token: str,
        handler,
        *,
        timeout: int = 25,
        allowed_updates=None,
        retry: RetryPolicy | None = None,
        api_base: str = API_BASE,
        error_backoff: float = 3.0,
    ):
        if not token:
            raise ValueError("bot token is required")
        if timeout < 0:
            raise ValueError("timeout must be 0 or more")
        self._session = session
        self._url = f"{api_base.rstrip('/')}/bot{token}/getUpdates"
        self._handler = handler
        self._timeout = timeout
        self._allowed = list(allowed_updates) if allowed_updates is not None else None
        self._retry = retry or RetryPolicy(max_retries=2, base_delay=0.5)
        self._error_backoff = error_backoff
        self._offset = None
        self._stopped = False

    @property
    def offset(self):
        """Next update_id Telegram will send, or None before the first update."""
        return self._offset

    async def get_updates(self) -> list:
        payload = {"timeout": self._timeout}
        if self._offset is not None:
            payload["offset"] = self._offset
        if self._allowed is not None:
            payload["allowed_updates"] = self._allowed
        response = await self._retry.send(
            self._session, "POST", self._url, json=payload, timeout=self._timeout + 10
        )
        if response.status != 200:
            raise TelegramError(f"HTTP {response.status}")
        data = response.json()
        if not isinstance(data, dict):
            raise TelegramError("unexpected reply")
        if not data.get("ok"):
            raise TelegramError(str(data.get("description", "unknown error")))
        return list(data.get("result", []))

    async def run_once(self) -> int:
        """Fetch one batch and dispatch it. Returns how many updates were handled."""
        updates = await self.get_updates()
        for update in updates:
            self._offset = update["update_id"] + 1  # confirm before handling, so a crash does not loop
            try:
                await self._handler(update)
            except Exception:
                log.exception("telegram handler failed for update_id=%r", update.get("update_id"))
        return len(updates)

    async def run(self) -> None:
        """Poll until stop() is called. Errors back off and polling continues."""
        while not self._stopped:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except (ClientError, OSError, asyncio.TimeoutError, TelegramError) as exc:
                log.warning("telegram polling error: %s", type(exc).__name__)
                await asyncio.sleep(self._error_backoff)

    def stop(self) -> None:
        self._stopped = True


class TelegramWebhook:
    """Receive updates over HTTPS and manage the webhook with the Bot API.

    Requests are checked against `secret_token` (sent by Telegram in a header) and handled by
    WebhookRouter: answered at once, run in the background, and deduplicated by update_id.
    """

    def __init__(
        self,
        session,
        token: str,
        handler,
        *,
        secret_token: str,
        api_base: str = API_BASE,
        retry: RetryPolicy | None = None,
        max_tasks: int = 1000,
        dedupe_size: int = 10_000,
        dedupe_ttl: float = 3600.0,
    ):
        if not token:
            raise ValueError("bot token is required")
        if not secret_token or not _SECRET_RE.fullmatch(secret_token):
            raise ValueError("secret_token must be 1-256 characters: A-Z, a-z, 0-9, _ or -")
        self._session = session
        self._api = f"{api_base.rstrip('/')}/bot{token}"
        self._secret = secret_token
        self._retry = retry or RetryPolicy(max_retries=2, base_delay=0.5)
        self._router = WebhookRouter(
            handler,
            secret_token=secret_token,
            dedupe_size=dedupe_size,
            dedupe_ttl=dedupe_ttl,
            max_tasks=max_tasks,
        )

    async def _call(self, method: str, payload: dict | None = None):
        response = await self._retry.send(
            self._session, "POST", f"{self._api}/{method}", json=payload or {}, timeout=30
        )
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and not data.get("ok", response.status == 200):
            raise TelegramError(str(data.get("description", "unknown error")))
        if response.status != 200:
            raise TelegramError(f"HTTP {response.status}")
        if not isinstance(data, dict):
            raise TelegramError("unexpected reply")
        return data.get("result")

    async def set_webhook(
        self,
        url: str,
        *,
        allowed_updates=None,
        drop_pending: bool = False,
        max_connections: int | None = None,
    ) -> bool:
        if not url.startswith("https://"):
            raise ValueError("Telegram webhook URL must use https://")
        payload = {
            "url": url,
            "secret_token": self._secret,
            "drop_pending_updates": drop_pending,
        }
        if allowed_updates is not None:
            payload["allowed_updates"] = list(allowed_updates)
        if max_connections is not None:
            payload["max_connections"] = int(max_connections)
        return bool(await self._call("setWebhook", payload))

    async def delete_webhook(self, *, drop_pending: bool = False) -> bool:
        return bool(await self._call("deleteWebhook", {"drop_pending_updates": drop_pending}))

    async def get_webhook_info(self) -> dict:
        info = await self._call("getWebhookInfo")
        return info if isinstance(info, dict) else {}

    async def handle(self, req):
        """The HTTP handler: checks the secret, answers 200 and dispatches the update."""
        return await self._router.handle(req)

    def attach(self, app, path: str = "/telegram"):
        """Register the POST route on a Server."""
        app.route(path, method="POST")(self.handle)
        return self.handle

    async def close(self, timeout: float = 10.0) -> None:
        """Stop accepting updates and wait up to `timeout` seconds for running handlers."""
        await self._router.close(timeout)
