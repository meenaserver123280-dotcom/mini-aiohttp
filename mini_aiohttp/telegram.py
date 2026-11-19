"""Telegram Bot API polling: getUpdates with offset tracking, dispatching each update to a handler.

    async def on_update(update: dict) -> None:
        ...

    poller = TelegramPoller(session, token, on_update)
    await poller.run()          # runs until poller.stop() is called

Use either polling (this module) or a webhook (WebhookRouter), not both: Telegram does not
deliver updates by getUpdates while a webhook is set.

The bot token is only put into the request URL. It is never logged. Errors are logged by
class name and Telegram's description, without the URL.
"""
from __future__ import annotations

import asyncio
import logging

from .client import ClientError
from .retry import RetryPolicy

log = logging.getLogger("mini_aiohttp.telegram")

API_BASE = "https://api.telegram.org"


class TelegramError(Exception):
    """Telegram answered with ok=false. `description` is Telegram's message, without the token."""

    def __init__(self, description: str):
        super().__init__(description)
        self.description = description


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
