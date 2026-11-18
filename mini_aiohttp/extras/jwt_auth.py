"""JWT bearer authentication middleware (optional).

Requires: pip install "mini-aiohttp[jwt]"

    from mini_aiohttp.extras.jwt_auth import jwt_auth

    app.middleware_(jwt_auth(secret="change-me", audience="my-api", exempt=("/health",)))

On success the decoded claims are available as req.user. Missing or invalid tokens
get 401 with a WWW-Authenticate header.
"""
from __future__ import annotations

from ..response import Response


def _jwt():
    try:
        import jwt
    except ImportError as exc:
        raise RuntimeError('JWT auth needs: pip install "mini-aiohttp[jwt]"') from exc
    return jwt


def jwt_auth(
    secret,
    *,
    algorithms=("HS256",),
    audience: str | None = None,
    issuer: str | None = None,
    exempt=(),
):
    """Require a valid 'Authorization: Bearer <token>' header on every request."""
    jwt = _jwt()
    exempt_paths = tuple(exempt)
    algs = list(algorithms)

    async def middleware(req, call_next):
        if req.path in exempt_paths:
            return await call_next(req)
        header = req.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return Response(401, "Unauthorized", {"WWW-Authenticate": "Bearer"})
        try:
            req.user = jwt.decode(
                token.strip(), secret, algorithms=algs, audience=audience, issuer=issuer,
            )
        except jwt.PyJWTError:
            return Response(401, "Unauthorized", {"WWW-Authenticate": 'Bearer error="invalid_token"'})
        return await call_next(req)

    return middleware
