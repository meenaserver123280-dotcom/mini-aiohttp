"""Response body decoding for Content-Encoding: gzip, x-gzip, deflate and br.

gzip and deflate use zlib's bounded-output decompressor. br needs the optional
'brotli' package; its Python API does not expose a max-output argument, so its
output is checked immediately after each process() call but cannot be hard-capped
inside the Brotli decoder itself.
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
    def __init__(self, name: str, max_output_size: int | None = None):
        self.name = name
        self.max_output_size = max_output_size
        self._output_size = 0
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

    def _record_output(self, output: bytes) -> bytes:
        if self.max_output_size is not None:
            if self._output_size + len(output) > self.max_output_size:
                raise ClientError(
                    f"Decompressed response body exceeds max_response_size "
                    f"({self.max_output_size} bytes)"
                )
        self._output_size += len(output)
        return output

    def _zlib_feed(self, data: bytes) -> bytes:
        if self.max_output_size is None:
            try:
                return self._record_output(self._z.decompress(data))
            except zlib.error as exc:
                raise ClientError("Malformed compressed response body") from exc

        remaining = self.max_output_size - self._output_size
        try:
            # Ask zlib for at most one byte beyond the remaining budget. That
            # extra byte proves the stream exceeds the configured limit.
            output = self._z.decompress(data, remaining + 1)
        except zlib.error as exc:
            raise ClientError("Malformed compressed response body") from exc

        if len(output) > remaining or self._z.unconsumed_tail:
            raise ClientError(
                f"Decompressed response body exceeds max_response_size "
                f"({self.max_output_size} bytes)"
            )
        return self._record_output(output)

    def feed(self, data: bytes) -> bytes:
        if not data:
            return b""
        if self.name == "br":
            try:
                output = self._z.process(data)
            except Exception as exc:
                raise ClientError("Malformed brotli response body") from exc
            # The brotli Python API currently has no bounded-output operation.
            return self._record_output(output)
        try:
            return self._zlib_feed(data)
        except ClientError:
            if self._raw_fallback and "Malformed compressed" in str(__import__("sys").exc_info()[1]):
                self._raw_fallback = False
                self._z = zlib.decompressobj(-zlib.MAX_WBITS)
                try:
                    return self._zlib_feed(data)
                except (zlib.error, ClientError) as exc:
                    if isinstance(exc, ClientError) and "max_response_size" in str(exc):
                        raise
                    raise ClientError("Malformed compressed response body") from exc
            raise

    def flush(self) -> bytes:
        if self.name == "br":
            return b""
        try:
            output = self._z.flush()
        except zlib.error as exc:
            raise ClientError("Truncated compressed response body") from exc
        return self._record_output(output)


class ContentDecoder:
    """Incremental Content-Encoding decoder with a per-layer output-size limit.

    Usage: out = d.feed(chunk) for each chunk, then tail = d.finish() once at the end.
    For gzip/deflate, zlib is asked to produce at most the remaining budget plus
    one byte, so a compression bomb is rejected before an unbounded output buffer
    is created. Brotli is checked after each process() call because its Python
    decoder API does not currently expose an output-length parameter.
    """

    def __init__(self, header_value: str | None, *, max_output_size: int | None = None):
        if max_output_size is not None and max_output_size < 0:
            raise ValueError("max_output_size must be non-negative or None")
        names = _encodings_of(header_value or "")
        # Encodings are listed in the order they were applied; undo them in reverse.
        self._steps = [
            _Step(name, max_output_size=max_output_size)
            for name in reversed(names)
        ]

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
