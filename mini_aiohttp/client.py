"""Async HTTP/1.1 client: pooling, proxies (HTTP, SOCKS5), DNS cache, redirects, retries,
cookies, streaming downloads and uploads, multipart forms, content decoding, hooks."""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import socket
from collections import defaultdict, deque
from urllib.parse import urljoin, urlsplit

from . import _optional
from ._encoding import ACCEPT_ENCODING, ContentDecoder
from .cookies import CookieJar
from .exceptions import ClientError
from .multipart import encode_multipart
from .response import Response, check_header
from .socks import open_socks5

__all__ = ["ClientSession", "ClientError"]

log = logging.getLogger("mini_aiohttp.client")

DEFAULT_MAX_RESPONSE_SIZE = 1024 * 1024 * 1024  # 1 GiB. Pass None to disable the limit.
MAX_HEADER_COUNT = 200
MAX_LINE = 64 * 1024
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
DEFAULT_RETRY_STATUSES = frozenset({429, 502, 503, 504})
MAX_RETRY_AFTER = 60.0
MAX_BACKOFF = 10.0
_METHOD_RE = re.compile(r"[A-Za-z]{1,20}")
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_LENGTH_RE = re.compile(r"[0-9]+")
_CHUNK = 64 * 1024
# Framing headers are computed by the client; callers must not set them.
_FRAMING_HEADERS = frozenset({"content-length", "transfer-encoding"})


def _host_header(hostname: str, port: int, tls: bool) -> str:
    """Host header value. IPv6 literals need brackets, e.g. [::1]:8080."""
    host = f"[{hostname}]" if ":" in hostname else hostname
    default = 443 if tls else 80
    return host if port == default else f"{host}:{port}"


def _origin(url: str):
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, (parts.hostname or "").lower(), port


def _is_streaming(body) -> bool:
    return hasattr(body, "__aiter__")


def _with_default(headers: dict, name: str, value: str) -> dict:
    """Add a default header unless the caller already set one, in any letter case."""
    if any(k.lower() == name.lower() for k in headers):
        return dict(headers)
    return {name: value, **headers}


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _readline(reader) -> bytes:
    try:
        return await reader.readuntil(b"\r\n")
    except asyncio.LimitOverrunError:
        raise ClientError("Response line too long")


class _StreamBody:
    """Async iterator over a streamed response body, decoding Content-Encoding on the way.

    The connection goes back to the pool only if the body was read to the end.
    aclose() releases the connection even if the body was never iterated.
    """

    def __init__(self, session, reader, framing, key, conn, reusable, decoder=None):
        self._gen = session._body_chunks(reader, framing)
        self._session = session
        self._key = key
        self._conn = conn
        self._reusable = reusable
        self._decoder = decoder
        self._done = False
        self._ended = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        while not self._ended:
            try:
                chunk = await self._gen.__anext__()
                if self._decoder is not None:
                    chunk = self._decoder.feed(chunk)
            except StopAsyncIteration:
                self._ended = True
                try:
                    tail = self._decoder.finish() if self._decoder is not None else b""
                except BaseException:
                    self._finish(complete=False)
                    raise
                self._finish(complete=True)
                if tail:
                    return tail
                break
            except BaseException:
                self._ended = True
                self._finish(complete=False)
                raise
            if chunk:
                return chunk
        raise StopAsyncIteration

    def _finish(self, complete: bool) -> None:
        if self._done:
            return
        self._done = True
        if complete and self._reusable:
            self._session._put_idle(self._key, self._conn)
        else:
            self._conn[1].close()

    async def aclose(self) -> None:
        self._ended = True
        try:
            await self._gen.aclose()
        finally:
            self._finish(complete=False)


class ClientSession:
    """Async HTTP client.

    Retries only apply to idempotent methods (GET, HEAD, PUT, DELETE, OPTIONS, TRACE)
    and never to streaming request bodies. POST and PATCH are never retried.

    Responses with Content-Encoding (gzip, deflate, and br when mini-aiohttp[brotli]
    is installed) are decoded automatically; Accept-Encoding is sent by default.

    proxy: "http://host:port", "socks5://", or "socks5h://". HTTPS targets use
    HTTP CONNECT or a SOCKS5 tunnel, then establish TLS end-to-end.
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        connect_timeout: float = 5.0,
        max_per_host: int = 1000,
        max_idle_per_host: int = 100,
        max_response_size: int | None = DEFAULT_MAX_RESPONSE_SIZE,
        max_redirects: int = 10,
        retries: int = 0,
        retry_statuses=DEFAULT_RETRY_STATUSES,
        backoff: float = 0.5,
        cookies: bool = True,
        ssl_context=None,
        proxy: str | None = None,
        dns_cache_ttl: float = 300.0,
        on_request=(),
        on_response=(),
        user_agent: str = "mini-aiohttp",
    ):
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.max_per_host = max_per_host
        self.max_idle_per_host = max_idle_per_host
        self.max_response_size = max_response_size
        self.max_redirects = max_redirects
        self.retries = retries
        self.retry_statuses = frozenset(retry_statuses)
        self.backoff = backoff
        self.cookie_jar = CookieJar() if cookies else None
        self.ssl_context = ssl_context
        self.dns_cache_ttl = dns_cache_ttl
        self.proxy = proxy
        self._proxy_key = None  # http proxy: the connection endpoint
        self._socks = None      # socks proxy: (host, port, user, password, remote_dns)
        if proxy is not None:
            p = urlsplit(proxy)
            if not p.hostname:
                raise ValueError("proxy URL has no host")
            if p.scheme == "http":
                self._proxy_key = (False, p.hostname, p.port or 80)
            elif p.scheme in ("socks5", "socks5h"):
                self._socks = (p.hostname, p.port or 1080, p.username, p.password, p.scheme == "socks5h")
            else:
                raise ValueError("proxy must start with http://, socks5://, or socks5h://")
        self.on_request = list(on_request)    # callables: (method, url, headers)
        self.on_response = list(on_response)  # callables: (response)
        self.user_agent = user_agent
        self._dns: dict = {}                    # host -> (address, expires_at)
        self._idle: dict = defaultdict(deque)  # key -> deque of (reader, writer)
        self._limits: dict = {}                 # key -> Semaphore
        self._closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def get(self, url: str, **kwargs) -> Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> Response:
        return await self.request("POST", url, **kwargs)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        data=None,
        json=None,
        form: dict | None = None,
        files: dict | None = None,
        timeout: float | None = None,
        stream: bool = False,
        allow_redirects: bool = True,
    ) -> Response:
        """Send a request.

        data: str or bytes body, or an async iterable of chunks (streamed upload, sent with
        Transfer-Encoding: chunked). json: any JSON value. form/files: multipart/form-data;
        files maps a field name to (filename, bytes, content_type). stream=True reads the
        response body lazily (see Response.iter_chunks).
        """
        if self._closed:
            raise ClientError("session is closed")
        method = method.upper()
        if not _METHOD_RE.fullmatch(method):
            raise ValueError(f"Invalid HTTP method: {method!r}")
        if json is not None and (form or files):
            raise ValueError("Use either json= or form/files=, not both")

        headers = dict(headers or {})
        for name in headers:
            if name.lower() in _FRAMING_HEADERS:
                raise ValueError(f"{name} is set by the client and cannot be passed in headers")
        streaming = _is_streaming(data)
        if streaming:
            if json is not None or form or files:
                raise ValueError("A streaming body cannot be combined with json, form or files")
        elif form or files:
            data, content_type = encode_multipart(form, files)
            headers = _with_default(headers, "Content-Type", content_type)
        elif json is not None:
            data = _optional.json_dumps(json)
            headers = _with_default(headers, "Content-Type", "application/json")
        elif isinstance(data, str):
            data = data.encode("utf-8")
        for name, value in headers.items():
            check_header(name, value)  # raises ValueError before anything is sent

        redirects = 0
        while True:
            resp = await self._send_with_retry(method, url, headers, data, timeout, stream)
            if not allow_redirects or resp.status not in REDIRECT_STATUSES or "location" not in resp.headers:
                return resp

            redirects += 1
            if redirects > self.max_redirects:
                await resp.aclose()
                raise ClientError(f"Too many redirects (limit {self.max_redirects})")
            await resp.aread()  # drain the redirect body so the connection can be reused

            if streaming and resp.status in (307, 308):
                raise ClientError("Cannot follow a redirect that must resend a streaming body")
            new_url = urljoin(url, resp.headers["location"])
            if urlsplit(new_url).scheme not in ("http", "https"):
                raise ClientError(f"Refusing redirect to unsupported URL: {new_url!r}")
            # 303 always becomes GET. 301 and 302 become GET when they were sent as POST.
            if resp.status == 303 or (resp.status in (301, 302) and method == "POST"):
                method = "GET"
                data = None
                streaming = False
                headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            # Never leak credentials to a different origin.
            if _origin(new_url) != _origin(url):
                headers = {
                    k: v for k, v in headers.items()
                    if k.lower() not in ("authorization", "proxy-authorization")
                }
            url = new_url

    # ---------- retries ----------
    def _backoff_delay(self, attempt: int, retry_after) -> float:
        if retry_after is not None and retry_after.strip().isdigit():
            return min(float(retry_after), MAX_RETRY_AFTER)
        return min(self.backoff * (2 ** attempt), MAX_BACKOFF)

    async def _send_with_retry(self, method, url, headers, body, timeout, stream):
        retryable = method in IDEMPOTENT_METHODS and not _is_streaming(body)
        attempt = 0
        while True:
            try:
                resp = await self._send_once(method, url, headers, body, timeout, stream)
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
                if not retryable or attempt >= self.retries:
                    raise
                delay = self._backoff_delay(attempt, None)
                log.debug("retrying %s %s after %r (attempt %d)", method, url, exc, attempt + 1)
                await asyncio.sleep(delay)
                attempt += 1
                continue

            if retryable and resp.status in self.retry_statuses and attempt < self.retries:
                delay = self._backoff_delay(attempt, resp.headers.get("retry-after"))
                await resp.aread()
                await asyncio.sleep(delay)
                attempt += 1
                continue
            return resp

    # ---------- one request on the wire ----------
    async def _send_once(self, method, url, headers, body, timeout, stream):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"Unsupported URL: {url!r}")
        tls = parts.scheme == "https"
        port = parts.port or (443 if tls else 80)
        key = (tls, parts.hostname, port)
        host_header = _host_header(parts.hostname, port, tls)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")

        if self._proxy_key is not None and tls:
            # A CONNECT tunnel is dedicated to one origin; never pool it across hosts.
            conn_key = (True, self._proxy_key[1], self._proxy_key[2], parts.hostname, port)
            request_target = target
        elif self._proxy_key is not None:
            conn_key = self._proxy_key
            request_target = parts._replace(fragment="").geturl()  # absolute-form for the proxy
        else:
            conn_key = key
            request_target = target

        streaming = _is_streaming(body)
        if streaming:
            content_length = None
        else:
            content_length = None if body is None else len(body)

        send_headers = dict(headers)
        if self.cookie_jar is not None and not any(k.lower() == "cookie" for k in send_headers):
            cookie = self.cookie_jar.header_for(url)
            if cookie:
                send_headers["Cookie"] = cookie
        for hook in self.on_request:
            await _maybe_await(hook(method, url, send_headers))

        head = self._build_head(method, request_target, host_header, send_headers, content_length, streaming)
        limit = self._limits.get(conn_key)
        if limit is None:
            limit = self._limits[conn_key] = asyncio.Semaphore(self.max_per_host)
        effective_timeout = self.timeout if timeout is None else timeout
        async with limit:
            resp = await asyncio.wait_for(
                self._roundtrip(conn_key, head, body, method, stream), effective_timeout
            )

        if self.cookie_jar is not None and resp.set_cookies:
            self.cookie_jar.update(url, resp.set_cookies)
        for hook in self.on_response:
            await _maybe_await(hook(resp))
        return resp

    def _build_head(self, method, target, host_header, headers, content_length, chunked) -> bytes:
        defaults = {
            "Host": host_header,
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Encoding": ACCEPT_ENCODING,
        }
        if chunked:
            defaults["Transfer-Encoding"] = "chunked"
        elif content_length is not None:
            defaults["Content-Length"] = str(content_length)
        caller = headers or {}
        caller_names = {k.lower() for k in caller}
        # A caller header replaces a default of the same name in any letter case,
        # so the request never carries the same header twice.
        h = {k: v for k, v in defaults.items() if k.lower() not in caller_names}
        h.update(caller)
        lines = [f"{method} {target} HTTP/1.1"] + [f"{k}: {v}" for k, v in h.items()]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

    # ---------- DNS and connections ----------
    async def _resolve(self, host: str) -> str:
        if self.dns_cache_ttl <= 0:
            return host
        loop = asyncio.get_running_loop()
        now = loop.time()
        cached = self._dns.get(host)
        if cached is not None and cached[1] > now:
            return cached[0]
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        address = infos[0][4][0]
        self._dns[host] = (address, now + self.dns_cache_ttl)
        return address

    def _tls_context(self):
        return self.ssl_context if self.ssl_context is not None else _optional.default_ssl_context()

    async def _connect(self, key):
        # HTTP proxy HTTPS tunnel: (True, proxy_host, proxy_port, target_host, target_port)
        if len(key) == 5:
            _, proxy_host, proxy_port, host, port = key
            proxy_address = await self._resolve(proxy_host)
            reader, writer = await asyncio.open_connection(proxy_address, proxy_port)
            try:
                authority_host = f"[{host}]" if ":" in host else host
                authority = f"{authority_host}:{port}"
                writer.write(
                    f"CONNECT {authority} HTTP/1.1\r\n"
                    f"Host: {authority}\r\n"
                    "Proxy-Connection: Keep-Alive\r\n\r\n".encode("ascii")
                )
                await writer.drain()
                status_line = await _readline(reader)
                try:
                    parts = status_line.decode("latin-1").strip().split(" ", 2)
                    status = int(parts[1])
                except (ValueError, IndexError):
                    raise ClientError("Malformed HTTP proxy CONNECT response")
                while await _readline(reader) != b"\r\n":
                    pass
                if status != 200:
                    raise ClientError(f"HTTP proxy CONNECT failed with status {status}")
                await writer.start_tls(self._tls_context(), server_hostname=host)
                return reader, writer
            except BaseException:
                writer.close()
                raise

        tls, host, port = key
        if self._socks is not None:
            proxy_host, proxy_port, user, password, remote_dns = self._socks
            target = host if remote_dns else await self._resolve(host)
            reader, writer = await open_socks5(proxy_host, proxy_port, target, port, user, password)
            if tls:
                try:
                    await writer.start_tls(self._tls_context(), server_hostname=host)
                except BaseException:
                    writer.close()
                    raise
            return reader, writer
        address = await self._resolve(host)
        if tls:
            ssl_arg = self._tls_context()
        else:
            ssl_arg = None
        return await asyncio.open_connection(
            address, port, ssl=ssl_arg, server_hostname=host if tls else None
        )

    def _take_idle(self, key):
        pool = self._idle.get(key)
        while pool:
            reader, writer = pool.pop()
            if not writer.is_closing():
                return reader, writer
        return None

    def _put_idle(self, key, conn):
        pool = self._idle[key]
        if len(pool) < self.max_idle_per_host:
            pool.append(conn)
        else:
            conn[1].close()

    async def _roundtrip(self, key, head: bytes, body, method: str, stream: bool) -> Response:
        conn = self._take_idle(key)
        if conn is not None:
            try:
                return await self._exchange(key, conn, head, body, method, stream)
            except (ConnectionError, asyncio.IncompleteReadError) as exc:
                # The server closed this pooled socket. The request may have reached it, so
                # only idempotent methods with a replayable body are sent again on a new socket.
                if method not in IDEMPOTENT_METHODS or _is_streaming(body):
                    raise ClientError(
                        f"Connection dropped on a reused socket; not retrying {method}"
                    ) from exc
        conn = await asyncio.wait_for(self._connect(key), self.connect_timeout)
        return await self._exchange(key, conn, head, body, method, stream)

    async def _write_body(self, writer, body) -> None:
        if body is None:
            await writer.drain()
            return
        if isinstance(body, (bytes, bytearray, memoryview)):
            writer.write(body)
            await writer.drain()
            return
        async for chunk in body:
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            if not chunk:
                continue
            writer.write(f"{len(chunk):x}\r\n".encode("ascii") + bytes(chunk) + b"\r\n")
            await writer.drain()
        writer.write(b"0\r\n\r\n")
        await writer.drain()

    async def _exchange(self, key, conn, head: bytes, body, method: str, stream: bool) -> Response:
        reader, writer = conn
        try:
            writer.write(head)
            await self._write_body(writer, body)
            status, headers, set_cookies, reusable, framing = await self._read_head_and_framing(
                reader, method
            )
            decoder = None
            if method != "HEAD" and status not in (204, 304):
                candidate = ContentDecoder(headers.get("content-encoding"))
                if candidate.active:
                    decoder = candidate
                    headers.pop("content-encoding", None)
                    headers.pop("content-length", None)  # no longer the decoded size
            if stream and framing[0] != "none":
                resp = Response(status, b"", headers)
                resp.set_cookies = set_cookies
                resp._chunks = _StreamBody(self, reader, framing, key, conn, reusable, decoder)
                return resp
            payload = b"".join([c async for c in self._body_chunks(reader, framing)])
            if decoder is not None:
                payload = decoder.feed(payload) + decoder.finish()
                self._check_size(len(payload))
        except BaseException:
            writer.close()
            raise
        if reusable:
            self._put_idle(key, conn)
        else:
            writer.close()
        resp = Response(status, payload, headers)
        resp.set_cookies = set_cookies
        return resp

    # ---------- response parsing ----------
    def _check_size(self, size: int) -> None:
        if self.max_response_size is not None and size > self.max_response_size:
            raise ClientError(
                f"Response body is larger than max_response_size ({self.max_response_size} bytes)"
            )

    async def _read_head(self, reader):
        line = await _readline(reader)
        try:
            parts = line.decode("latin-1").rstrip("\r\n").split(" ", 2)
            version, status = parts[0], int(parts[1])
        except (ValueError, IndexError):
            raise ClientError("Malformed status line")
        if not version.startswith("HTTP/") or not 100 <= status <= 999:
            raise ClientError("Malformed status line")

        headers: dict = {}
        set_cookies: list = []
        while True:
            line = await _readline(reader)
            if line == b"\r\n":
                break
            if len(headers) + len(set_cookies) >= MAX_HEADER_COUNT:
                raise ClientError("Too many response headers")
            if line[:1] in (b" ", b"\t"):
                raise ClientError("Obsolete line folding in response")
            name, sep, value = line.decode("latin-1").rstrip("\r\n").partition(":")
            if not sep or not _HEADER_NAME_RE.fullmatch(name):
                raise ClientError("Malformed response header")
            key = name.lower()
            if key == "set-cookie":
                set_cookies.append(value.strip())
                headers[key] = value.strip()  # last value, for convenience
                continue
            if key == "content-length" and key in headers:
                raise ClientError("Duplicate Content-Length in response")
            headers[key] = value.strip()
        return version, status, headers, set_cookies

    async def _read_head_and_framing(self, reader, method: str):
        while True:
            version, status, headers, set_cookies = await self._read_head(reader)
            if 100 <= status < 200:
                if status == 101:
                    raise ClientError("Protocol upgrades are not supported")
                continue  # interim response such as 100 Continue: wait for the final one
            break

        connection = headers.get("connection", "").lower()
        if version == "HTTP/1.1":
            reusable = "close" not in connection
        else:
            reusable = "keep-alive" in connection

        if method == "HEAD" or status in (204, 304):
            framing = ("none",)
        elif "chunked" in headers.get("transfer-encoding", "").lower():
            framing = ("chunked",)
        elif "content-length" in headers:
            raw_len = headers["content-length"]
            if not _LENGTH_RE.fullmatch(raw_len):
                raise ClientError("Invalid Content-Length in response")
            size = int(raw_len)
            self._check_size(size)
            framing = ("length", size)
        else:
            framing = ("eof",)  # body ends when the server closes the connection
            reusable = False
        return status, headers, set_cookies, reusable, framing

    async def _body_chunks(self, reader, framing):
        kind = framing[0]
        if kind == "none":
            return
        if kind == "length":
            remaining = framing[1]
            while remaining > 0:
                part = await reader.read(min(_CHUNK, remaining))
                if not part:
                    raise asyncio.IncompleteReadError(b"", remaining)
                remaining -= len(part)
                yield part
            return
        if kind == "eof":
            total = 0
            while True:
                part = await reader.read(_CHUNK)
                if not part:
                    return
                total += len(part)
                self._check_size(total)
                yield part
        # chunked transfer encoding
        total = 0
        while True:
            size_line = await _readline(reader)
            try:
                size = int(size_line.split(b";")[0].strip(), 16)
            except ValueError:
                raise ClientError("Malformed chunk size")
            if size < 0:
                raise ClientError("Malformed chunk size")
            if size == 0:
                trailers = 0
                while await _readline(reader) != b"\r\n":
                    trailers += 1
                    if trailers > MAX_HEADER_COUNT:
                        raise ClientError("Too many chunk trailers")
                return
            total += size
            self._check_size(total)
            yield await reader.readexactly(size)
            await reader.readexactly(2)  # CRLF after each chunk

    async def close(self) -> None:
        self._closed = True
        writers = []
        for pool in self._idle.values():
            while pool:
                _, writer = pool.pop()
                writer.close()
                writers.append(writer)
        for writer in writers:
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError) as exc:
                log.debug("error while closing connection: %s", exc)
