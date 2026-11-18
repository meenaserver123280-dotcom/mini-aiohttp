"""Response body decoding for Content-Encoding: gzip, x-gzip, deflate and br.

gzip and deflate use the standard library. br needs the optional 'brotli' package;
it is advertised in Accept-Encoding only when installed, so servers never send it otherwise.
"""
from __future__ import annotations

import zlib

from . import _optional
from .exceptions import ClientError


def accept_encoding_value() -> str:
    names = ["gzip", "deflate"]
    if _optional.HAS_BROTLI:
        names.append("br")
    return ", ".join(names)


ACCEPT_ENCODING = accept_encoding_value()


def _encodings_of(header_value: str) -> list:
    parts = [p.strip().lower() for p in (header_value or "").split(",")]
    return [p for p in parts if p and p != "identity"]


class _Step:
    def __init__(self, name: str):
        self.name = name
        self._raw_fallback = False
        if name in ("gzip", "x-gzip"):
            self._z = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif name == "deflate":
            # RFC 1950 (zlib wrapper) is what servers send; some send raw RFC 1951 instead.
            self._z = zlib.decompressobj(zlib.MAX_WBITS)
            self._raw_fallback = True
        elif name == "br":
            if not _optional.HAS_BROTLI:
                raise ClientError("Response is brotli-encoded; install mini-aiohttp[brotli]")
            self._z = _optional.brotli.Decompressor()
        else:
            raise ClientError(f"Unsupported Content-Encoding: {name}")

    def feed(self, data: bytes) -> bytes:
        if not data:
            return b""
        if self.name == "br":
            try:
                return self._z.process(data)
            except Exception as exc:
                raise ClientError("Malformed brotli response body") from exc
        try:
            return self._z.decompress(data)
        except zlib.error:
            if self._raw_fallback:
                self._raw_fallback = False
                self._z = zlib.decompressobj(-zlib.MAX_WBITS)
                try:
                    return self._z.decompress(data)
                except zlib.error:
                    pass
            raise ClientError("Malformed compressed response body")

    def flush(self) -> bytes:
        if self.name == "br":
            return b""
        try:
            return self._z.flush()
        except zlib.error:
            raise ClientError("Truncated compressed response body")


class ContentDecoder:
    """Decodes a body given its Content-Encoding header value.

    Usage: out = d.feed(chunk) for each chunk, then tail = d.finish() once at the end.
    """

    def __init__(self, header_value: str | None):
        names = _encodings_of(header_value or "")
        # Encodings are listed in the order they were applied; undo them in reverse.
        self._steps = [_Step(n) for n in reversed(names)]

    @property
    def active(self) -> bool:
        return bool(self._steps)

    def feed(self, data: bytes) -> bytes:
        for step in self._steps:
            data = step.feed(data)
        return data

    def finish(self) -> bytes:
        out = b""
        for step in self._steps:
            out = step.feed(out) + step.flush()
        return out
