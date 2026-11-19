"""ASGI 3 adapter: run a mini_aiohttp Server under uvicorn, hypercorn or any ASGI server.

The adapter does no routing of its own. Each HTTP request goes through
``Server._dispatch``, so routes, middleware, static files and the 404, 405 and 500
rules behave the same as on the built-in HTTP/1.1 server.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from .response import Request, Response

log = logging.getLogger("mini_aiohttp.asgi")

_FRAMING_HEADERS = frozenset({b"content-length", b"transfer-encoding"})
_DEFAULT_CONTENT_TYPE = b"text/plain; charset=utf-8"
_TOO_LARGE = object()  # sentinel: the request body is larger than max_body_size

Hook = Callable[[], Awaitable[None]]


class ASGIApp:
    """Wrap a Server so that ASGI servers can run it.

    WebSocket scopes are refused. Use WebSocketServer on its own port for WebSockets.
    """

    def __init__(
        self,
        server: Any,
        *,
        on_startup: Hook | None = None,
        on_shutdown: Hook | None = None,
    ) -> None:
        self.server = server
        self.on_startup = on_startup
        self.on_shutdown = on_shutdown

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        kind = scope["type"]
        if kind == "http":
            await self._handle_http(scope, receive, send)
        elif kind == "lifespan":
            await self._handle_lifespan(receive, send)
        elif kind == "websocket":
            # The Server has no WebSocket routing. Reject before the upgrade is accepted.
            await send({"type": "websocket.close", "code": 1000})
        else:
            raise ValueError(f"Unsupported ASGI scope type: {kind!r}")

    async def _handle_http(self, scope: dict, receive: Callable, send: Callable) -> None:
        server = self.server
        body = await _read_body(receive, server.max_body_size)
        if body is None:
            return  # client disconnected before the body finished: nothing to send
        if body is _TOO_LARGE:
            response = Response(413, "Request body too large")
        else:
            response = await server._dispatch(_build_request(scope, body))
        server._total_requests += 1
        server._status_counts[response.status] = server._status_counts.get(response.status, 0) + 1
        await _send_response(response, send, head_only=scope["method"].upper() == "HEAD")

    async def _handle_lifespan(self, receive: Callable, send: Callable) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    if self.on_startup is not None:
                        await self.on_startup()
                except Exception as exc:
                    log.exception("startup hook failed")
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                try:
                    if self.on_shutdown is not None:
                        await self.on_shutdown()
                except Exception as exc:
                    log.exception("shutdown hook failed")
                    await send({"type": "lifespan.shutdown.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.shutdown.complete"})
                return
            else:
                return


async def _read_body(receive: Callable, limit: int):
    """Read the whole request body. Returns bytes, None on disconnect, or _TOO_LARGE."""
    chunks: list = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        chunk = message.get("body") or b""
        total += len(chunk)
        if total > limit:
            return _TOO_LARGE
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _build_request(scope: dict, body: bytes) -> Request:
    # raw_path keeps percent-escapes intact, so routing decodes them exactly once,
    # as the built-in server does. Fall back to re-quoting the decoded path.
    raw_path = scope.get("raw_path")
    if raw_path:
        path = raw_path.decode("latin-1").partition("?")[0]
    else:
        path = quote(scope.get("path") or "/", safe="/")
    headers: dict = {}
    for raw_name, raw_value in scope.get("headers") or ():
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        if name in headers:
            separator = "; " if name == "cookie" else ", "
            headers[name] = f"{headers[name]}{separator}{value}"
        else:
            headers[name] = value
    version = "HTTP/1.0" if scope.get("http_version") == "1.0" else "HTTP/1.1"
    query = (scope.get("query_string") or b"").decode("latin-1")
    request = Request(scope["method"].upper(), path, query, version, headers, body)
    client = scope.get("client")
    request.client = client[0] if client else None
    return request


async def _send_response(response: Response, send: Callable, *, head_only: bool) -> None:
    headers: list = []
    for name, value in response.headers.items():
        raw_name = name.encode("latin-1")
        if raw_name not in _FRAMING_HEADERS:  # the ASGI server owns body framing
            headers.append((raw_name, str(value).encode("latin-1")))
    if not any(name == b"content-type" for name, _ in headers):
        headers.append((b"content-type", _DEFAULT_CONTENT_TYPE))
    headers.extend((b"set-cookie", raw.encode("latin-1")) for raw in response._out_set_cookies)
    known_length = getattr(response, "_known_length", None)
    if known_length is None and not response.is_streaming:
        known_length = len(response.body)
    if known_length is not None:
        headers.append((b"content-length", str(known_length).encode("ascii")))

    await send({"type": "http.response.start", "status": response.status, "headers": headers})
    if response.is_streaming:
        await _send_stream(response._out_chunks, send, head_only=head_only)
    else:
        body = b"" if head_only else response.body
        await send({"type": "http.response.body", "body": body, "more_body": False})


async def _send_stream(chunks: Any, send: Callable, *, head_only: bool) -> None:
    try:
        if not head_only:
            async for chunk in chunks:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("stream chunks must be bytes-like or str")
                payload = bytes(chunk)
                if payload:
                    await send({"type": "http.response.body", "body": payload, "more_body": True})
    finally:
        aclose = getattr(chunks, "aclose", None)
        if aclose is not None:
            await aclose()
    await send({"type": "http.response.body", "body": b"", "more_body": False})
