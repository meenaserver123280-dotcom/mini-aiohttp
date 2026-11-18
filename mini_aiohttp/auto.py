"""Automatic HTTPS protocol selection using ALPN and Alt-Svc discovery."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from .client import ClientSession
from .response import Response
from .http2 import HTTP2Client
from .http3 import HTTP3Client

_ALT_H3 = re.compile(r'(?:^|,)\s*h3(?:-[0-9]+)?="(?::([0-9]{1,5}))"')


class AutoClientSession:
    """Negotiate HTTP/2 with ALPN, fall back to HTTP/1.1, and learn HTTP/3
    endpoints from Alt-Svc for subsequent requests. The first request cannot
    assume HTTP/3; an origin must advertise QUIC support first.

    HTTP/2 is used only when no ssl_context is given. The HTTP/2 client has to
    set ALPN on the TLS context, and that context would then also be used for
    HTTP/1.1 connections, which would break them. With a custom ssl_context,
    requests use HTTP/1.1 (and HTTP/3 when Alt-Svc advertises it).
    """
    def __init__(self, *, timeout=10.0, ssl_context=None, **client_options):
        self.timeout = timeout
        self.ssl_context = ssl_context
        self._http1 = ClientSession(timeout=timeout, ssl_context=ssl_context, **client_options)
        self._http2 = HTTP2Client(timeout=timeout) if ssl_context is None else None
        self._http3 = HTTP3Client(timeout=timeout)
        self._h3_ports = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        await self._http1.close()

    def _remember_alt_svc(self, host, value):
        if not value or value.strip().lower() == "clear":
            self._h3_ports.pop(host, None)
            return
        match = _ALT_H3.search(value)
        if match:
            port = int(match.group(1) or 443)
            if 1 <= port <= 65535:
                self._h3_ports[host] = port

    @staticmethod
    def _response(result):
        status, headers, body = result
        clean = {k.lower(): v for k, v in headers.items() if not k.startswith(":")}
        return Response(status, body, clean)

    async def request(self, method, url, *, headers=None, data=b""):
        parts = urlsplit(url)
        if parts.scheme != "https":
            return await self._http1.request(method, url, headers=headers, data=data)
        host = parts.hostname
        h3_port = self._h3_ports.get(host)
        if h3_port is not None:
            authority_host = f"[{host}]" if ":" in host else host
            h3_url = urlunsplit((parts.scheme, f"{authority_host}:{h3_port}", parts.path, parts.query, ""))
            try:
                result = await self._http3.request(method, h3_url, headers=headers, data=data)
                response = self._response(result)
                self._remember_alt_svc(host, response.headers.get("alt-svc"))
                return response
            except Exception:
                self._h3_ports.pop(host, None)
        if self._http2 is not None:
            try:
                result = await self._http2.request(method, url, headers=headers, data=data)
                response = self._response(result)
                self._remember_alt_svc(host, response.headers.get("alt-svc"))
                return response
            except RuntimeError as exc:
                if "did not negotiate HTTP/2" not in str(exc):
                    raise
        response = await self._http1.request(method, url, headers=headers, data=data)
        self._remember_alt_svc(host, response.headers.get("alt-svc"))
        return response

    async def get(self, url, **kwargs):
        return await self.request("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request("POST", url, **kwargs)


__all__ = ["AutoClientSession"]
