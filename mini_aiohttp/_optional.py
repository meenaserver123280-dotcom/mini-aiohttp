"""Optional accelerators. Each one is used only when it is installed; none is required.

    pip install "mini-aiohttp[brotli]"   # br Content-Encoding support
    pip install "mini-aiohttp[orjson]"   # faster JSON in Response.json / Request.json
    pip install "mini-aiohttp[certifi]"  # Mozilla CA bundle for HTTPS on systems with a broken store
    pip install "mini-aiohttp[speed]"    # orjson + uvloop (call use_uvloop() before asyncio.run)
"""
from __future__ import annotations

import asyncio
import json as _stdlib_json
import ssl as _ssl

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover - depends on the environment
    _orjson = None

try:
    import brotli as _brotli
except ImportError:  # pragma: no cover
    _brotli = None

try:
    import certifi as _certifi
except ImportError:  # pragma: no cover
    _certifi = None

brotli = _brotli
HAS_ORJSON = _orjson is not None
HAS_BROTLI = _brotli is not None
HAS_CERTIFI = _certifi is not None

_default_context = None


def json_dumps(data) -> bytes:
    """Serialize to UTF-8 JSON bytes, using orjson when installed."""
    if _orjson is not None:
        try:
            return _orjson.dumps(data)
        except TypeError:
            pass  # e.g. non-string dict keys: let the stdlib produce its usual error or output
    return _stdlib_json.dumps(data).encode("utf-8")


def json_loads(body: bytes):
    """Parse JSON bytes, using orjson when installed. Raises ValueError on bad input."""
    if _orjson is not None:
        return _orjson.loads(body)  # orjson.JSONDecodeError is a ValueError
    return _stdlib_json.loads(body)


def default_ssl_context() -> _ssl.SSLContext:
    """Shared client TLS context. Uses certifi's CA bundle when installed."""
    global _default_context
    if _default_context is None:
        if _certifi is not None:
            _default_context = _ssl.create_default_context(cafile=_certifi.where())
        else:
            _default_context = _ssl.create_default_context()
    return _default_context


def use_uvloop() -> bool:
    """Switch asyncio to uvloop if it is installed. Call before asyncio.run().

    Returns True when uvloop is now the event loop policy, False otherwise.
    """
    try:
        import uvloop
    except ImportError:
        return False
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    return True
