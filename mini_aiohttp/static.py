"""Static file middleware with HTTP Range requests, ETag and conditional GET.

Register it with ``app.middleware_(StaticFiles("/static", "public"))``. Requests
under the prefix are answered here; every other request goes to ``call_next``.
"""
from __future__ import annotations

import asyncio
import mimetypes
import re
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote

from .response import Request, Response

CHUNK_SIZE = 64 * 1024
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_UNSATISFIABLE = "unsatisfiable"


def _weak(tag: str) -> str:
    tag = tag.strip()
    return tag[2:] if tag.startswith("W/") else tag


def _not_modified(headers: dict, etag: str, mtime: float) -> bool:
    if_none_match = headers.get("if-none-match")
    if if_none_match is not None:
        if if_none_match.strip() == "*":
            return True
        return any(_weak(candidate) == _weak(etag) for candidate in if_none_match.split(","))
    if_modified_since = headers.get("if-modified-since")
    if if_modified_since:
        try:
            since = parsedate_to_datetime(if_modified_since).timestamp()
        except (TypeError, ValueError):
            return False
        return int(mtime) <= int(since)
    return False


def _byte_range(headers: dict, etag: str, last_modified: str, size: int):
    """Return (start, end) inclusive, None to send the whole file, or _UNSATISFIABLE."""
    raw = headers.get("range")
    if raw is None:
        return None
    if_range = headers.get("if-range")
    if if_range is not None and if_range.strip() not in (etag, last_modified):
        return None  # validator does not match: the client gets the whole file
    match = _RANGE_RE.fullmatch(raw.strip())
    if match is None:
        return None  # malformed or multi-range: ignore Range (RFC 9110 allows this)
    first, last = match.groups()
    if not first and not last:
        return None
    if size == 0:
        return _UNSATISFIABLE
    if not first:  # suffix range: last N bytes
        length = int(last)
        if length == 0:
            return _UNSATISFIABLE
        return max(size - length, 0), size - 1
    start = int(first)
    if start >= size:
        return _UNSATISFIABLE
    if last:
        end = int(last)
        if end < start:
            return None  # invalid range: ignore it
        return start, min(end, size - 1)
    return start, size - 1


async def _file_chunks(path: Path, start: int, length: int):
    """Stream `length` bytes from `start` in chunks, reading off the event loop."""
    loop = asyncio.get_running_loop()
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = await loop.run_in_executor(None, handle.read, min(CHUNK_SIZE, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


class StaticFiles:
    """Serve GET and HEAD requests for files under `prefix` from `directory`.

    Supports single byte ranges (206, 416), ETag and Last-Modified, If-None-Match,
    If-Modified-Since (304) and If-Range. Paths cannot leave `directory`.
    """

    def __init__(
        self,
        prefix: str,
        directory,
        *,
        max_file_size: int = 50 * 1024 * 1024,
        cache_control: str = "public, max-age=3600",
    ) -> None:
        base = Path(directory).resolve()
        if not base.is_dir():
            raise ValueError(f"Static directory does not exist: {directory}")
        cleaned = prefix.strip("/")
        self.prefix = f"/{cleaned}" if cleaned else ""
        self.base = base
        self.max_file_size = max_file_size
        self.cache_control = cache_control

    def _matches(self, path: str) -> bool:
        if not self.prefix:
            return True
        return path == self.prefix or path.startswith(self.prefix + "/")

    def _resolve(self, path: str) -> Path | None:
        relative = unquote(path[len(self.prefix):]).lstrip("/")
        try:
            target = (self.base / relative).resolve()
            target.relative_to(self.base)
        except (ValueError, OSError):
            return None
        return target if target.is_file() else None

    async def __call__(self, req: Request, call_next) -> Response:
        if not self._matches(req.path):
            return await call_next(req)
        if req.method not in ("GET", "HEAD"):
            return Response(405, "Method Not Allowed", {"Allow": "GET, HEAD"})
        return self._serve(req)

    def _serve(self, req: Request) -> Response:
        target = self._resolve(req.path)
        if target is None:
            return Response(404, "Not Found")
        stat = target.stat()
        size = stat.st_size
        if size > self.max_file_size:
            return Response(413, "Payload Too Large")

        etag = f'"{size:x}-{stat.st_mtime_ns:x}"'
        last_modified = formatdate(stat.st_mtime, usegmt=True)
        headers = {
            "ETag": etag,
            "Last-Modified": last_modified,
            "Accept-Ranges": "bytes",
            "Cache-Control": self.cache_control,
        }
        if _not_modified(req.headers, etag, stat.st_mtime):
            return Response(304, b"", headers)

        headers["Content-Type"] = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        byte_range = _byte_range(req.headers, etag, last_modified, size)
        head_only = req.method == "HEAD"

        if byte_range == _UNSATISFIABLE:
            return Response(416, "Range Not Satisfiable", {**headers, "Content-Range": f"bytes */{size}"})

        if byte_range is None:
            status, start, length = 200, 0, size
        else:
            start, end = byte_range
            status, length = 206, end - start + 1
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"

        if head_only:
            response = Response(status, b"", headers)
        else:
            response = Response.stream(_file_chunks(target, start, length), status=status, headers=headers)
        # A known length makes the server send Content-Length instead of chunked framing.
        response._known_length = length
        return response
