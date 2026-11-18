"""Request and Response objects shared by the client and the server."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs

from . import _optional
from .multipart import parse_multipart

REASONS = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    304: "Not Modified",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
    505: "HTTP Version Not Supported",
}

_TOKEN_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_UNSAFE_VALUE_RE = re.compile(r"[\x00\r\n]")


def check_header(name, value) -> None:
    """Raise ValueError for header names or values that could split or inject headers."""
    if not isinstance(name, str) or not _TOKEN_RE.fullmatch(name):
        raise ValueError(f"Invalid header name: {name!r}")
    if _UNSAFE_VALUE_RE.search(str(value)):
        raise ValueError(f"Invalid characters in value of header {name!r}")


@dataclass
class Request:
    method: str
    path: str
    query: str
    version: str
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    params: dict = field(default_factory=dict)  # filled by the router, e.g. {"id": "42"}
    client: str | None = None                   # peer IP address, set by the server

    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self):
        """Parse the body as JSON. Raises ValueError on invalid JSON."""
        return _optional.json_loads(self.body)

    def query_params(self) -> dict:
        """Parsed query string as {name: [values]}."""
        return parse_qs(self.query, keep_blank_values=True)

    def form(self) -> dict:
        """Parsed multipart/form-data body as {name: [str | UploadedFile, ...]}.

        Raises ValueError for bad bodies. Catch it in your handler and return 400.
        """
        return parse_multipart(self.body, self.headers.get("content-type", ""))


class Response:
    def __init__(self, status: int = 200, body=b"", headers: dict | None = None):
        self.status = status
        self.body = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        self.headers: dict = {}
        for name, value in (headers or {}).items():
            check_header(name, value)
            # Header names are stored lowercase so lookups are case-insensitive.
            self.headers[name.lower()] = str(value)
        self.set_cookies: list = []     # raw Set-Cookie values (client responses)
        self._out_set_cookies: list = []  # Set-Cookie lines to send (server responses)
        self._chunks = None             # async iterator of body chunks when streaming (client)
        self._out_chunks = None         # async iterator when streaming a server response

    @classmethod
    def stream(cls, chunks, status: int = 200, headers: dict | None = None) -> "Response":
        """Create a chunked response from an async iterable of bytes or text chunks.

        The response is streamed without buffering the complete payload in memory.
        The server closes the connection if iteration fails after headers are sent.
        """
        if not hasattr(chunks, "__aiter__"):
            raise TypeError("chunks must be an async iterable")
        response = cls(status, b"", headers)
        response._out_chunks = chunks
        return response

    @classmethod
    def json(cls, data, status: int = 200) -> "Response":
        return cls(
            status,
            _optional.json_dumps(data),
            {"Content-Type": "application/json; charset=utf-8"},
        )

    def text(self, encoding: str = "utf-8") -> str:
        """Body decoded as text. Only for buffered responses; use aread() for streams first."""
        if self._chunks is not None:
            raise RuntimeError("streamed body not read yet; call await aread() first")
        return self.body.decode(encoding)

    def add_set_cookie(self, raw: str) -> None:
        """Queue a Set-Cookie header. Several cookies can be added."""
        check_header("Set-Cookie", raw)
        self._out_set_cookies.append(raw)

    # ---------- body access (client, including streamed responses) ----------
    async def iter_chunks(self):
        """Yield the body in chunks. Works for buffered and streamed responses."""
        if self._chunks is None:
            if self.body:
                yield self.body
            return
        async for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        """Read the rest of a streamed body into memory and return it."""
        if self._chunks is not None:
            parts = []
            async for chunk in self._chunks:
                parts.append(chunk)
            self.body = b"".join(parts)
            self._chunks = None
        return self.body

    async def aclose(self) -> None:
        """Stop reading a streamed body and release its connection."""
        if self._chunks is not None:
            await self._chunks.aclose()
            self._chunks = None

    @property
    def is_streaming(self) -> bool:
        return self._out_chunks is not None

    @property
    def reason(self) -> str:
        return REASONS.get(self.status, "Unknown")

    def to_bytes(self, keep_alive: bool = True) -> bytes:
        if self.is_streaming:
            raise ValueError("Streaming responses must be sent by the server")
        headers = dict(self.headers)
        headers.setdefault("content-type", "text/plain; charset=utf-8")
        headers["content-length"] = str(len(self.body))
        headers["connection"] = "keep-alive" if keep_alive else "close"
        # Middleware may have changed headers after __init__, so validate again.
        for name, value in headers.items():
            check_header(name, value)
        head = [f"HTTP/1.1 {self.status} {REASONS.get(self.status, 'Unknown')}"]
        head += [f"{k.title()}: {v}" for k, v in headers.items()]
        head += [f"Set-Cookie: {c}" for c in self._out_set_cookies]
        return ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + self.body

    def __repr__(self) -> str:
        return f"<Response {self.status} ({len(self.body)} bytes)>"
