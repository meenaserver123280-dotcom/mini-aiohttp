"""Authentication and CSRF protection: basic auth, API keys and CSRF tokens.

    app.middleware_(csrf_protect(secret=os.environ["CSRF_SECRET"]))   # unsafe requests need a token

    @app.route("/form")
    async def form(req):
        return Response(200, f'<input type="hidden" name="csrf_token" value="{req.csrf_token}">')

    @app.route("/admin")
    @route_middleware(basic_auth({"admin": os.environ["ADMIN_PASSWORD"]}))
    async def admin(req): ...

    @app.route("/api/items")
    @route_middleware(api_key_auth(os.environ["API_KEYS"].split(",")))
    async def items(req): ...

Client address: `req.client` is the TCP peer address. These helpers never read X-Forwarded-For
or any other address header, so a client cannot spoof its IP.

Passwords and keys are compared as SHA-256 digests with hmac.compare_digest. Failed checks get
401 or 403 with no detail about what was wrong.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from .response import Request, Response
from .security import set_cookie

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _read_cookie(headers: dict, name: str):
    raw = headers.get("cookie")
    if not raw:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return None
    morsel = jar.get(name)
    return morsel.value if morsel is not None else None


def csrf_protect(
    secret: str | bytes,
    *,
    cookie_name: str = "csrf_token",
    header_name: str = "x-csrf-token",
    form_field: str = "csrf_token",
    max_age: int = 86400,
    secure: bool = True,
    samesite: str = "Lax",
):
    """Double-submit CSRF protection with signed tokens.

    Each visitor gets a random token in a cookie (HttpOnly, SameSite=Lax). Templates put
    `req.csrf_token` into forms. An unsafe request (POST, PUT, PATCH, DELETE) must send the same
    token in the X-CSRF-Token header or in a form field, or it gets 403. Tokens are signed with
    HMAC-SHA256, so an attacker cannot plant a cookie that passes.
    """
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if len(key) < 32:
        raise ValueError("CSRF secret must be at least 32 bytes")
    if max_age <= 0:
        raise ValueError("max_age must be positive")
    header_name = header_name.lower()

    def mac(nonce: str) -> str:
        raw = hmac.new(key, nonce.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def valid(token: str) -> bool:
        nonce, sep, signature = token.partition(".")
        if not sep or not nonce or not signature or not (nonce.isascii() and signature.isascii()):
            return False
        return hmac.compare_digest(signature.encode("ascii"), mac(nonce).encode("ascii"))

    def new_token() -> str:
        nonce = secrets.token_urlsafe(32)
        return f"{nonce}.{mac(nonce)}"

    def submitted(req: Request):
        value = req.headers.get(header_name)
        if value is None and req.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            fields = parse_qs(req.body.decode("utf-8", "replace"), keep_blank_values=True)
            found = fields.get(form_field)
            value = found[0] if found else None
        return value

    async def middleware(req: Request, call_next) -> Response:
        current = _read_cookie(req.headers, cookie_name)
        if current is not None and not valid(current):
            current = None
        if req.method not in SAFE_METHODS:
            sent = submitted(req)
            if (
                current is None
                or sent is None
                or not hmac.compare_digest(sent.encode("utf-8"), current.encode("utf-8"))
            ):
                return Response(403, "CSRF token missing or invalid")
        issue = current is None
        if issue:
            current = new_token()
        req.csrf_token = current
        resp = await call_next(req)
        if issue:
            set_cookie(
                resp, cookie_name, current, max_age=max_age,
                path="/", secure=secure, httponly=True, samesite=samesite,
            )
        return resp

    return middleware


def _challenge(realm: str) -> Response:
    safe_realm = realm.replace('"', "")
    return Response(401, "Unauthorized", {"WWW-Authenticate": f'Basic realm="{safe_realm}", charset="UTF-8"'})


def basic_auth(users: dict | None = None, *, realm: str = "Restricted", verify=None):
    """HTTP Basic authentication. Pass `users` ({username: password}) or `verify(user, password) -> bool`.

    On success the username is available as `req.user`.
    """
    if (users is None) == (verify is None):
        raise ValueError("pass exactly one of users or verify")
    digests = {} if users is None else {name: _digest(pw) for name, pw in users.items()}

    def check(user: str, password: str) -> bool:
        if verify is not None:
            return bool(verify(user, password))
        candidate = _digest(password)
        expected = digests.get(user)
        if expected is None:
            hmac.compare_digest(candidate, candidate)  # same work as a real check
            return False
        return hmac.compare_digest(expected, candidate)

    async def middleware(req: Request, call_next) -> Response:
        scheme, _, token = req.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "basic" or not token.strip():
            return _challenge(realm)
        try:
            decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return _challenge(realm)
        user, sep, password = decoded.partition(":")
        if not sep or not check(user, password):
            return _challenge(realm)
        req.user = user
        return await call_next(req)

    return middleware


def api_key_auth(keys=None, *, header: str = "x-api-key", verify=None):
    """Check an API key sent in a request header. Pass `keys` (strings) or `verify(key) -> bool`.

    Keys are not accepted in the URL, because URLs end up in logs.
    """
    if (keys is None) == (verify is None):
        raise ValueError("pass exactly one of keys or verify")
    digests: list = []
    if keys is not None:
        digests = [_digest(k) for k in keys if k]
        if not digests:
            raise ValueError("keys must contain at least one non-empty key")
    header = header.lower()

    def accepted(presented: str) -> bool:
        if verify is not None:
            return bool(verify(presented))
        candidate = _digest(presented)
        matched = False
        for expected in digests:  # check every key, so timing does not reveal which one matched
            if hmac.compare_digest(expected, candidate):
                matched = True
        return matched

    async def middleware(req: Request, call_next) -> Response:
        presented = req.headers.get(header)
        if not presented or not accepted(presented):
            return Response(401, "Unauthorized")
        return await call_next(req)

    return middleware
