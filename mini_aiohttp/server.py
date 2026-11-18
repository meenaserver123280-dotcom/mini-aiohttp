"""Asyncio HTTP/1.1 server: routing, middleware for every request, static files, HTTPS, graceful shutdown."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .response import Request, Response, check_header

log = logging.getLogger("mini_aiohttp.server")

MAX_LINE = 8 * 1024  # max size of one request or header line
MAX_HEADER_COUNT = 100
STATIC_CHUNK = 64 * 1024
_TOKEN_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_METHOD_RE = re.compile(r"[A-Za-z]{1,20}")
_DIGITS_RE = re.compile(r"[0-9]+")
_HEX_RE = re.compile(r"[0-9A-Fa-f]+")
_SUPPORTED_VERSIONS = ("HTTP/1.0", "HTTP/1.1")
# Headers that must not appear twice: duplicates are a request-smuggling risk.
_SINGLETON_HEADERS = ("content-length", "transfer-encoding", "host")


class BadRequest(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(message or str(status))
        self.status = status


async def _readline(reader: asyncio.StreamReader) -> bytes:
    try:
        return await reader.readuntil(b"\r\n")
    except asyncio.LimitOverrunError:
        raise BadRequest(431, "Line too long")


def _split_path(path: str) -> list:
    return [seg for seg in path.split("/") if seg]


def _param_count(pattern: list) -> int:
    return sum(1 for seg in pattern if seg.startswith("{") and seg.endswith("}"))


def _match_pattern(pattern: list, segments: list):
    """Return a dict of captured params if the path matches the pattern, else None."""
    if len(pattern) != len(segments):
        return None
    params = {}
    for p, s in zip(pattern, segments):
        if p.startswith("{") and p.endswith("}"):
            params[p[1:-1]] = s
        elif p != s:
            return None
    return params


def _parse_target(target: str):
    """Split a request target into (path, query). Supports origin-form and absolute-form."""
    try:
        if target.startswith(("http://", "https://")):
            parts = urlsplit(target)
            return parts.path or "/", parts.query
    except ValueError:
        raise BadRequest(400, "Malformed request target")
    path, _, query = target.partition("?")
    if not path.startswith("/"):
        raise BadRequest(400, "Unsupported request target")
    return path, query


def _check_fd_limit(needed: int) -> None:
    """Warn early if the OS will refuse sockets before we hit max_connections."""
    try:
        import resource
    except ImportError:  # Windows
        return
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and soft < needed:
        log.warning(
            "Open file limit is %d but about %d sockets are needed. "
            "Raise it with `ulimit -n %d`.",
            soft, needed, needed,
        )


def _const(response: Response):
    async def terminal(request: Request) -> Response:
        return response
    return terminal


def _route_terminal(handler):
    async def terminal(request: Request) -> Response:
        result = await handler(request)
        return result if isinstance(result, Response) else Response(200, result)
    return terminal


def _wrap(mw, nxt):
    async def inner(req: Request) -> Response:
        return await mw(req, nxt)
    return inner


def _head_only(data: bytes, body_length: int) -> bytes:
    """Keep the status line and headers of a serialized response, but report the GET
    body length in Content-Length and drop the body (RFC 9110: HEAD has no body)."""
    head_end = data.index(b"\r\n\r\n") + 4
    lines = data[: head_end - 4].split(b"\r\n")
    lines = [
        b"Content-Length: %d" % body_length if line.lower().startswith(b"content-length:") else line
        for line in lines
    ]
    return b"\r\n".join(lines) + b"\r\n\r\n"


def _file_chunks(path: Path):
    """Async generator that reads a file in chunks without blocking the event loop."""
    async def chunks():
        loop = asyncio.get_running_loop()
        with path.open("rb") as handle:
            while True:
                chunk = await loop.run_in_executor(None, handle.read, STATIC_CHUNK)
                if not chunk:
                    return
                yield chunk
    return chunks()


async def _close_iterator(iterator) -> None:
    aclose = getattr(iterator, "aclose", None)
    if aclose is not None:
        await aclose()


class Server:
    def __init__(
        self,
        *,
        max_connections: int = 100_000,
        read_timeout: float = 15.0,
        max_body_size: int = 10 * 1024 * 1024,
        backlog: int = 4096,
    ):
        self.routes: list = []       # public route registrations: (method, pattern_segments, handler)
        self._exact_routes: dict = {}  # (method, decoded path segments) -> handler
        self._exact_paths: set = set()  # decoded paths registered for any method
        self._param_routes: list = []  # parameterized routes only
        self.middleware: list = []   # async fn(request, call_next) -> Response
        self.max_connections = max_connections
        self.read_timeout = read_timeout
        self.max_body_size = max_body_size
        self.backlog = backlog
        self._static_roots: list = []  # (prefix, resolved_dir, max_file_size)
        self._active = 0
        self._total_connections = 0
        self._total_requests = 0
        self._status_counts: dict = {}
        self._writers: set = set()
        self._tasks: set = set()     # one task per open connection, for graceful shutdown
        self._draining = False
        self._server = None

    # ---------- routing ----------
    def route(self, path: str, method: str = "GET"):
        """Register a handler. Use {name} in the path to capture a segment: '/users/{id}'.

        A GET route also answers HEAD requests when no HEAD route is registered.
        """
        def decorator(handler):
            method_name = method.upper()
            pattern = _split_path(path)
            self.routes.append((method_name, pattern, handler))
            if _param_count(pattern):
                self._param_routes.append((method_name, pattern, handler))
                # Keep parameter routes ordered by specificity; sorting only this small
                # subset avoids scanning literal routes on every request.
                self._param_routes.sort(key=lambda route: _param_count(route[1]))
            else:
                key = tuple(pattern)
                # Preserve the original first-registration-wins behavior.
                self._exact_routes.setdefault((method_name, key), handler)
                self._exact_paths.add(key)
            return handler
        return decorator

    def middleware_(self, fn):
        """Register middleware. It runs for EVERY request, including 404, 405 and static files."""
        self.middleware.append(fn)
        return fn

    def static(self, prefix: str, directory, *, max_file_size: int = 50 * 1024 * 1024) -> None:
        """Serve GET and HEAD requests under `prefix` from `directory`.

        Paths are resolved and must stay inside `directory`, so '..' and symlinks
        cannot reach other files. Files are streamed in chunks, never read whole.
        """
        base = Path(directory).resolve()
        if not base.is_dir():
            raise ValueError(f"Static directory does not exist: {directory}")
        self._static_roots.append((prefix.rstrip("/"), base, max_file_size))

    def _static_response(self, req: Request):
        for prefix, base, max_size in self._static_roots:
            if req.path != prefix and not req.path.startswith(prefix + "/"):
                continue
            if req.method not in ("GET", "HEAD"):
                return Response(405, "Method Not Allowed")
            rel = unquote(req.path[len(prefix):]).lstrip("/")
            try:
                target = (base / rel).resolve()
                target.relative_to(base)
            except (ValueError, OSError):
                return Response(404, "Not Found")
            if not target.is_file():
                return Response(404, "Not Found")
            size = target.stat().st_size
            if size > max_size:
                return Response(413, "Payload Too Large")
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if req.method == "HEAD":
                response = Response(200, b"", {"Content-Type": content_type})
                response._known_length = size
                return response
            response = Response.stream(_file_chunks(target), headers={"Content-Type": content_type})
            response._known_length = size
            return response
        return None

    def _match(self, method: str, path: str):
        # Decode each segment once. Exact routes use O(1) dictionary lookup; only
        # parameterized routes need pattern matching.
        segments = tuple(unquote(seg) for seg in _split_path(path))
        handler = self._exact_routes.get((method, segments))
        if handler is not None:
            return handler, {}, True

        path_seen = segments in self._exact_paths
        for route_method, pattern, route_handler in self._param_routes:
            params = _match_pattern(pattern, segments)
            if params is None:
                continue
            path_seen = True
            if route_method == method:
                return route_handler, params, True
        return None, None, path_seen

    async def _dispatch(self, req: Request) -> Response:
        try:
            handler, params, path_seen = self._match(req.method, req.path)
            if handler is None and req.method == "HEAD":
                handler, params, path_seen = self._match("GET", req.path)
            if handler is not None:
                req.params = params
                terminal = _route_terminal(handler)
            else:
                fixed = self._static_response(req)
                if fixed is None:
                    fixed = (
                        Response(405, "Method Not Allowed") if path_seen
                        else Response(404, "Not Found")
                    )
                terminal = _const(fixed)
            chain = terminal
            for mw in reversed(self.middleware):
                chain = _wrap(mw, chain)
            return await chain(req)
        except Exception:
            log.exception("handler failed: %s %s", req.method, req.path)
            return Response(500, "Internal Server Error")

    # ---------- connection handling ----------
    async def _on_connect(self, reader, writer):
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            if self._draining or self._active >= self.max_connections:
                # Refuse fast instead of queueing: keeps memory bounded under overload.
                await self._send(writer, Response(503, "Server busy"), keep_alive=False)
                return
            self._active += 1
            self._total_connections += 1
            self._writers.add(writer)
            try:
                await self._serve_connection(reader, writer)
            finally:
                self._active -= 1
                self._writers.discard(writer)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass  # client went away mid-request; nothing to do
        except Exception:
            log.exception("unexpected error in connection handler")
        finally:
            self._tasks.discard(task)
            writer.close()

    async def _serve_connection(self, reader, writer):
        peer = writer.get_extra_info("peername")
        client_ip = peer[0] if peer else None
        while True:
            try:
                # asyncio.timeout avoids creating a helper Task for every keep-alive
                # request (wait_for() does), reducing per-request scheduling overhead.
                async with asyncio.timeout(self.read_timeout):
                    request = await self._read_request(reader, writer)
            except BadRequest as exc:
                await self._send(writer, Response(exc.status, str(exc)), keep_alive=False)
                return
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                return  # idle or slow client: drop the connection
            if request is None:
                return  # clean EOF
            request.client = client_ip
            self._total_requests += 1

            keep_alive = self._wants_keep_alive(request) and not self._draining
            response = await self._dispatch(request)
            self._status_counts[response.status] = self._status_counts.get(response.status, 0) + 1
            keep_alive = await self._send(writer, response, keep_alive, head_only=request.method == "HEAD")
            if not keep_alive:
                return

    async def _read_headers(self, reader) -> dict:
        headers: dict = {}
        while True:
            raw = await _readline(reader)
            if raw == b"\r\n":
                return headers
            if len(headers) >= MAX_HEADER_COUNT:
                raise BadRequest(431, "Too many headers")
            if raw[:1] in (b" ", b"\t"):
                raise BadRequest(400, "Obsolete line folding is not allowed")
            text = raw.decode("latin-1").rstrip("\r\n")
            name, sep, value = text.partition(":")
            # No whitespace allowed in the name: "Host : x" is a classic smuggling trick.
            if not sep or not _TOKEN_RE.fullmatch(name):
                raise BadRequest(400, "Malformed header")
            if "\x00" in value:
                raise BadRequest(400, "Invalid header value")
            key = name.lower()
            if key in headers and key in _SINGLETON_HEADERS:
                raise BadRequest(400, f"Duplicate {name} header")
            headers[key] = value.strip()

    async def _read_request(self, reader, writer=None) -> Request | None:
        # RFC 9112 section 2.2: ignore empty lines received before a request line.
        while True:
            try:
                line = await _readline(reader)
            except asyncio.IncompleteReadError as exc:
                if not exc.partial:
                    return None
                raise
            if line not in (b"\r\n", b"\n"):
                break

        try:
            method, target, version = line.decode("latin-1").rstrip("\r\n").split(" ")
        except ValueError:
            raise BadRequest(400, "Malformed request line")
        if not _METHOD_RE.fullmatch(method):
            raise BadRequest(400, "Invalid request method")
        if version not in _SUPPORTED_VERSIONS:
            raise BadRequest(505, "HTTP version not supported")
        path, query = _parse_target(target)

        headers = await self._read_headers(reader)
        has_transfer_encoding = "transfer-encoding" in headers
        has_content_length = "content-length" in headers
        if has_transfer_encoding and has_content_length:
            raise BadRequest(400, "Content-Length and Transfer-Encoding cannot be combined")

        # Expect: 100-continue: tell the client to send the body now, instead of waiting.
        if (
            writer is not None
            and version == "HTTP/1.1"
            and headers.get("expect", "").lower() == "100-continue"
        ):
            writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
            await writer.drain()

        if has_transfer_encoding:
            encodings = [item.strip().lower() for item in headers["transfer-encoding"].split(",")]
            if version != "HTTP/1.1" or encodings != ["chunked"]:
                raise BadRequest(501, "Only HTTP/1.1 chunked request bodies are supported")
            body = await self._read_chunked_body(reader)
        else:
            raw_len = headers.get("content-length", "0")
            if not _DIGITS_RE.fullmatch(raw_len):
                raise BadRequest(400, "Invalid Content-Length")
            length = int(raw_len)
            if length > self.max_body_size:
                raise BadRequest(413, "Request body too large")
            body = await reader.readexactly(length) if length else b""

        return Request(method.upper(), path, query, version, headers, body)

    async def _read_chunked_body(self, reader) -> bytes:
        """Read a bounded HTTP/1.1 chunked request body, including optional trailers."""
        chunks = []
        total = 0
        while True:
            line = await _readline(reader)
            try:
                size_token = line[:-2].decode("ascii").split(";", 1)[0].strip()
            except UnicodeDecodeError:
                raise BadRequest(400, "Invalid chunk size")
            if not size_token or not _HEX_RE.fullmatch(size_token):
                raise BadRequest(400, "Invalid chunk size")
            size = int(size_token, 16)
            if size == 0:
                break
            total += size
            if total > self.max_body_size:
                raise BadRequest(413, "Request body too large")
            chunk = await reader.readexactly(size)
            if await reader.readexactly(2) != b"\r\n":
                raise BadRequest(400, "Chunk data is not terminated by CRLF")
            chunks.append(chunk)

        # Consume and validate trailers. Framing and routing headers are forbidden here.
        trailer_count = 0
        forbidden = {"content-length", "transfer-encoding", "host", "connection", "trailer"}
        while True:
            raw = await _readline(reader)
            if raw == b"\r\n":
                break
            trailer_count += 1
            if trailer_count > MAX_HEADER_COUNT:
                raise BadRequest(431, "Too many trailer headers")
            if raw[:1] in (b" ", b"\t"):
                raise BadRequest(400, "Obsolete trailer folding is not allowed")
            text = raw.decode("latin-1").rstrip("\r\n")
            name, sep, value = text.partition(":")
            if not sep or not _TOKEN_RE.fullmatch(name) or "\x00" in value:
                raise BadRequest(400, "Malformed trailer header")
            if name.lower() in forbidden:
                raise BadRequest(400, "Forbidden trailer header")
        return b"".join(chunks)

    @staticmethod
    def _wants_keep_alive(req: Request) -> bool:
        conn = req.headers.get("connection", "").lower()
        if req.version == "HTTP/1.1":
            return "close" not in conn
        return "keep-alive" in conn

    @staticmethod
    async def _send(writer, response: Response, keep_alive: bool, head_only: bool = False) -> bool:
        """Write a buffered or streamed response; return whether reuse is safe.

        head_only: answer to a HEAD request. Only the headers are written.
        """
        if response.is_streaming or getattr(response, "_known_length", None) is not None:
            return await Server._send_streamed(writer, response, keep_alive, head_only)
        try:
            data = response.to_bytes(keep_alive)
        except ValueError:
            log.exception("handler produced an invalid response; sending 500")
            data = Response(500, "Internal Server Error").to_bytes(keep_alive=False)
            keep_alive = False
        if head_only:
            data = _head_only(data, len(response.body))
        writer.write(data)
        await writer.drain()
        return keep_alive

    @staticmethod
    async def _send_streamed(writer, response: Response, keep_alive: bool, head_only: bool) -> bool:
        known_length = getattr(response, "_known_length", None)
        headers = dict(response.headers)
        headers.setdefault("content-type", "application/octet-stream")
        headers["connection"] = "keep-alive" if keep_alive else "close"
        headers.pop("content-length", None)
        headers.pop("transfer-encoding", None)
        if known_length is not None:
            headers["content-length"] = str(known_length)  # fixed length: no chunk framing
        else:
            headers["transfer-encoding"] = "chunked"
        try:
            for name, value in headers.items():
                check_header(name, value)
            head = [f"HTTP/1.1 {response.status} {response.reason}"]
            head.extend(f"{name.title()}: {value}" for name, value in headers.items())
            head.extend(f"Set-Cookie: {value}" for value in response._out_set_cookies)
            writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
            await writer.drain()
            if head_only:
                await _close_iterator(response._out_chunks)
                return keep_alive
            chunked = known_length is None
            async for chunk in response._out_chunks:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("stream chunks must be bytes-like or str")
                payload = bytes(chunk)
                if not payload:
                    continue
                if chunked:
                    writer.write(f"{len(payload):x}\r\n".encode("ascii") + payload + b"\r\n")
                else:
                    writer.write(payload)
                await writer.drain()
            if chunked:
                writer.write(b"0\r\n\r\n")
                await writer.drain()
            return keep_alive
        except Exception:
            log.exception("streaming response failed; closing connection")
            return False

    # ---------- lifecycle ----------
    async def start(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        *,
        reuse_port: bool = False,
        ssl=None,
    ):
        """Start listening. Pass an ssl.SSLContext (server side) to serve HTTPS."""
        _check_fd_limit(self.max_connections + 64)
        self._server = await asyncio.start_server(
            self._on_connect,
            host,
            port,
            backlog=self.backlog,
            limit=MAX_LINE,
            reuse_port=reuse_port,
            ssl=ssl,
        )
        return self._server

    async def serve_forever(self, host: str = "127.0.0.1", port: int = 8080, **kwargs):
        server = await self.start(host, port, **kwargs)
        log.info("Listening on %s:%d", host, port)
        async with server:
            await server.serve_forever()

    @property
    def stats(self) -> dict:
        """Return a snapshot of lightweight server counters."""
        return {
            "active_connections": self._active,
            "total_connections": self._total_connections,
            "total_requests": self._total_requests,
            "status_counts": dict(self._status_counts),
            "uptime_requests_per_connection": (
                self._total_requests / self._total_connections if self._total_connections else 0.0
            ),
        }

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def close(self, *, grace: float = 5.0) -> None:
        """Stop accepting, let in-flight requests finish for up to `grace` seconds, then close.

        Never raises on a slow shutdown: anything still open after `grace` is cut off.
        """
        self._draining = True
        if self._server is not None:
            self._server.close()
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=grace)
        for task in list(self._tasks):
            task.cancel()
        for writer in list(self._writers):
            writer.close()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        if self._server is not None:
            try:
                await asyncio.wait_for(self._server.wait_closed(), grace)
            except asyncio.TimeoutError:
                log.warning("server did not finish closing within %.1fs", grace)