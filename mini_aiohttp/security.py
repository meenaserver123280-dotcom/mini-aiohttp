"""Security middleware and helpers: CORS, security headers, rate limiting, IP policy,
secure cookies, and JSON input validation.

All middleware here follows the server signature: async fn(request, call_next).
"""
from __future__ import annotations

import ipaddress
import math
import re
import time
from urllib.parse import quote

from .response import Response

_COOKIE_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def _append_vary(headers: dict, token: str) -> None:
    """Add a token to the Vary header without dropping tokens another middleware set."""
    existing = headers.get("vary")
    if not existing:
        headers["vary"] = token
        return
    tokens = [t.strip().lower() for t in existing.split(",")]
    if token.lower() not in tokens:
        headers["vary"] = f"{existing}, {token}"


# ---------- CORS ----------

def cors(
    *,
    allow_origins,
    allow_methods=("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"),
    allow_headers=("Content-Type", "Authorization"),
    allow_credentials: bool = False,
    max_age: int = 600,
):
    """Cross-origin resource sharing.

    Only origins in `allow_origins` are reflected back, by exact match. "*" is allowed
    only without credentials, and combining it with credentials raises ValueError.
    Preflight (OPTIONS with Access-Control-Request-Method) is answered with 204.
    """
    origins = set(allow_origins)
    wildcard = "*" in origins
    if wildcard and allow_credentials:
        raise ValueError("allow_credentials cannot be used with '*' origins")
    methods = ", ".join(m.upper() for m in allow_methods)
    headers_value = ", ".join(allow_headers)

    def allowed_origin(origin):
        if origin is None:
            return None
        if wildcard:
            return "*"
        return origin if origin in origins else None

    def add_cors(resp: Response, allowed: str) -> None:
        resp.headers["access-control-allow-origin"] = allowed
        _append_vary(resp.headers, "Origin")
        if allow_credentials:
            resp.headers["access-control-allow-credentials"] = "true"

    async def middleware(req, call_next):
        allowed = allowed_origin(req.headers.get("origin"))
        is_preflight = (
            req.method == "OPTIONS"
            and req.headers.get("access-control-request-method") is not None
        )
        if is_preflight:
            resp = Response(204, "")
            if allowed is not None:
                add_cors(resp, allowed)
                resp.headers["access-control-allow-methods"] = methods
                resp.headers["access-control-allow-headers"] = headers_value
                resp.headers["access-control-max-age"] = str(max_age)
            return resp
        resp = await call_next(req)
        if allowed is not None:
            add_cors(resp, allowed)
        return resp

    return middleware


# ---------- security headers ----------

def security_headers(*, csp: str | None = "default-src 'self'", hsts_max_age: int | None = None):
    """Add common hardening headers. Headers the handler already set are kept."""

    async def middleware(req, call_next):
        resp = await call_next(req)
        resp.headers.setdefault("x-content-type-options", "nosniff")
        resp.headers.setdefault("x-frame-options", "DENY")
        resp.headers.setdefault("referrer-policy", "no-referrer")
        if csp:
            resp.headers.setdefault("content-security-policy", csp)
        if hsts_max_age:
            resp.headers.setdefault(
                "strict-transport-security",
                f"max-age={int(hsts_max_age)}; includeSubDomains",
            )
        return resp

    return middleware


# ---------- rate limiting ----------

class RateLimiter:
    """Token-bucket rate limiter keyed by client IP.

    rate: tokens added per second. burst: bucket size. Requests over the limit get 429
    with a Retry-After header. Memory is bounded by max_clients (oldest entries drop first).
    """

    def __init__(self, *, rate: float, burst: int, max_clients: int = 10_000, clock=time.monotonic):
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst must be >= 1")
        self.rate = rate
        self.burst = burst
        self.max_clients = max_clients
        self._clock = clock
        self._buckets: dict = {}  # key -> (tokens, last_seen)

    def check(self, key: str):
        """Return None if the request is allowed, else seconds until the next token."""
        now = self._clock()
        tokens, last = self._buckets.get(key, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - last) * self.rate)
        if tokens >= 1.0:
            tokens -= 1.0
            wait = None
        else:
            wait = (1.0 - tokens) / self.rate
        self._buckets.pop(key, None)
        self._buckets[key] = (tokens, now)
        while len(self._buckets) > self.max_clients:
            self._buckets.pop(next(iter(self._buckets)))
        return wait

    def middleware(self):
        async def middleware(req, call_next):
            wait = self.check(req.client or "unknown")
            if wait is not None:
                retry = str(max(1, math.ceil(wait)))
                return Response(429, "Too Many Requests", {"Retry-After": retry})
            return await call_next(req)
        return middleware


# ---------- IP access policy ----------

def _normalize_address(address):
    """Map IPv4-mapped IPv6 addresses (::ffff:a.b.c.d) to plain IPv4, so IPv4 rules apply."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def ip_policy(*, allow=None, deny=None):
    """Allow or deny clients by IP or CIDR network, e.g. deny=["203.0.113.0/24"].

    deny is checked first. If allow is given, only clients inside those networks pass.
    Requests with an unknown client address are refused. IPv4-mapped IPv6 clients are
    checked as their IPv4 address, so an IPv4 rule cannot be bypassed through dual-stack.
    """
    allow_nets = None if allow is None else [ipaddress.ip_network(n, strict=False) for n in allow]
    deny_nets = [ipaddress.ip_network(n, strict=False) for n in (deny or [])]

    async def middleware(req, call_next):
        try:
            address = _normalize_address(ipaddress.ip_address(req.client or ""))
        except ValueError:
            return Response(403, "Forbidden")
        if any(address in net for net in deny_nets):
            return Response(403, "Forbidden")
        if allow_nets is not None and not any(address in net for net in allow_nets):
            return Response(403, "Forbidden")
        return await call_next(req)

    return middleware


# ---------- secure cookies ----------

def set_cookie(
    resp: Response,
    name: str,
    value: str,
    *,
    max_age: int | None = None,
    path: str = "/",
    secure: bool = True,
    httponly: bool = True,
    samesite: str = "Lax",
) -> Response:
    """Add a Set-Cookie header with safe defaults (Secure, HttpOnly, SameSite=Lax).

    The value is URL-encoded. Invalid names, paths, or SameSite=None without Secure raise ValueError.
    """
    if not _COOKIE_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid cookie name: {name!r}")
    if samesite not in ("Strict", "Lax", "None"):
        raise ValueError("samesite must be 'Strict', 'Lax', or 'None'")
    if samesite == "None" and not secure:
        raise ValueError("SameSite=None requires secure=True")
    if not path.startswith("/") or any(c in path for c in ";\r\n\x00"):
        raise ValueError("Invalid cookie path")
    parts = [f"{name}={quote(value, safe='')}", f"Path={path}", f"SameSite={samesite}"]
    if max_age is not None:
        parts.append(f"Max-Age={int(max_age)}")
    if secure:
        parts.append("Secure")
    if httponly:
        parts.append("HttpOnly")
    resp.add_set_cookie("; ".join(parts))
    return resp


# ---------- JSON input validation ----------

def _type_name(expected) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


def require_json(schema: dict):
    """Decorator for handlers: the JSON body must be an object with every field of the
    given type. Otherwise the handler is skipped and the client gets 400.

        @app.route("/users", method="POST")
        @require_json({"name": str, "age": int})
        async def create(req): ...

    A field type may be a tuple of types, e.g. {"score": (int, float)}.
    """

    def decorator(handler):
        async def wrapper(req):
            try:
                data = req.json()
            except ValueError:
                return Response(400, "Body must be valid JSON")
            if not isinstance(data, dict):
                return Response(400, "JSON body must be an object")
            for field_name, expected in schema.items():
                if field_name not in data:
                    return Response(400, f"Missing field: {field_name}")
                value = data[field_name]
                types = expected if isinstance(expected, tuple) else (expected,)
                # bool is a subclass of int in Python; reject it unless bool is requested.
                if isinstance(value, bool) and bool not in types:
                    wrong_type = True
                else:
                    wrong_type = not isinstance(value, types)
                if wrong_type:
                    return Response(400, f"Field {field_name} must be {_type_name(expected)}")
            return await handler(req)

        wrapper.__name__ = getattr(handler, "__name__", "handler")
        return wrapper

    return decorator
