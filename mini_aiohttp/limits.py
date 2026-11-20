"""Route-level middleware: a rate limit or any other middleware for one handler only.

    login_limiter = RateLimiter(rate=0.2, burst=5)

    @app.route("/login", method="POST")
    @route_limit(login_limiter)
    async def login(req): ...

    @app.route("/admin")
    @route_middleware(require_admin, audit_log)
    async def admin(req): ...

Global middleware (Server.middleware_) still runs first, for every request. Route middleware
runs inside it, only for the handler it decorates, in the order listed.
"""
from __future__ import annotations

from .response import Response


def _bind(middleware, nxt):
    async def call(req):
        return await middleware(req, nxt)
    return call


def route_middleware(*middlewares):
    """Wrap one handler with middleware, each with the server signature async fn(request, call_next)."""

    def decorator(handler):
        async def terminal(req):
            result = await handler(req)
            return result if isinstance(result, Response) else Response(200, result)

        chain = terminal
        for middleware in reversed(middlewares):
            chain = _bind(middleware, chain)

        async def wrapper(req):
            return await chain(req)

        wrapper.__name__ = getattr(handler, "__name__", "handler")
        wrapper.__doc__ = handler.__doc__
        return wrapper

    return decorator


def route_limit(limiter):
    """Rate limit one route. `limiter` is a RateLimiter; clients are keyed by IP."""
    return route_middleware(limiter.middleware())
