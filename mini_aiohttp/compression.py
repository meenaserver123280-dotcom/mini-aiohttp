"""Server-side response compression as middleware (standard library only).

    from mini_aiohttp import Server
    from mini_aiohttp.compression import gzip_middleware

    app = Server()
    app.middleware_(gzip_middleware(min_size=1024))

Compresses buffered responses when the client accepts gzip, the body is at least
min_size bytes, the content type is compressible and the handler did not already
set a Content-Encoding. Streamed responses are passed through unchanged.
"""
from __future__ import annotations

import gzip

_COMPRESSIBLE = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/xhtml+xml",
    "image/svg+xml",
)


def _accepts_gzip(header_value: str) -> bool:
    """True if Accept-Encoding allows gzip with a non-zero quality (or via *)."""
    for item in header_value.split(","):
        parts = [p.strip() for p in item.split(";")]
        name = parts[0].lower()
        if name not in ("gzip", "*"):
            continue
        quality = 1.0
        for param in parts[1:]:
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


def _add_vary(headers: dict, token: str) -> None:
    existing = headers.get("vary")
    if not existing:
        headers["vary"] = token
    elif token.lower() not in [v.strip().lower() for v in existing.split(",")]:
        headers["vary"] = f"{existing}, {token}"


def gzip_middleware(*, min_size: int = 1024, level: int = 6):
    """Return middleware that gzips eligible buffered responses."""
    if not 1 <= level <= 9:
        raise ValueError("level must be between 1 and 9")

    async def middleware(req, call_next):
        resp = await call_next(req)
        if resp.is_streaming or len(resp.body) < min_size:
            return resp
        if "content-encoding" in resp.headers:
            return resp
        if not _accepts_gzip(req.headers.get("accept-encoding", "")):
            return resp
        content_type = resp.headers.get("content-type", "")
        if not content_type.startswith(_COMPRESSIBLE):
            return resp
        resp.body = gzip.compress(resp.body, compresslevel=level)
        resp.headers["content-encoding"] = "gzip"
        _add_vary(resp.headers, "Accept-Encoding")
        return resp

    return middleware


__all__ = ["gzip_middleware"]
