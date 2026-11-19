"""Signed-cookie sessions.

The session data lives in the cookie itself, signed with HMAC-SHA256, so the server
keeps no state. The signature stops tampering. It does not hide the data, so keep
secrets out of the session. Stolen cookies stay valid until they expire.

    app.middleware_(SessionMiddleware(secret=os.environ["SESSION_SECRET"]))

    @app.route("/login", method="POST")
    async def login(req):
        req.session["user_id"] = 42
        return Response(200, "ok")
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from http.cookies import SimpleCookie

from .response import Request, Response
from .security import set_cookie

MAX_COOKIE_BYTES = 3800  # browsers keep at most about 4 KiB per cookie


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class Session:
    """Dict-like session data for one request. Changes mark it as modified."""

    def __init__(self, data: dict | None = None) -> None:
        self._data: dict = dict(data or {})
        self.modified = False
        self.cleared = False

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value) -> None:
        self._data[key] = value
        self.modified = True

    def __delitem__(self, key) -> None:
        del self._data[key]
        self.modified = True

    def __contains__(self, key) -> bool:
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def pop(self, key, default=None):
        self.modified = True
        return self._data.pop(key, default)

    def clear(self) -> None:
        self._data.clear()
        self.modified = True
        self.cleared = True

    def to_dict(self) -> dict:
        return dict(self._data)


class SessionMiddleware:
    """Load the session from the cookie, expose it as `req.session`, and save changes."""

    def __init__(
        self,
        secret: str | bytes,
        *,
        cookie_name: str = "session",
        max_age: int = 3600,
        path: str = "/",
        secure: bool = True,
        httponly: bool = True,
        samesite: str = "Lax",
        clock=time.time,
    ) -> None:
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if len(key) < 32:
            raise ValueError("session secret must be at least 32 bytes")
        if max_age <= 0:
            raise ValueError("max_age must be positive")
        self._key = key
        self.cookie_name = cookie_name
        self.max_age = max_age
        self.path = path
        self.secure = secure
        self.httponly = httponly
        self.samesite = samesite
        self._clock = clock

    def _sign(self, payload: bytes) -> str:
        return _b64encode(hmac.new(self._key, payload, hashlib.sha256).digest())

    def encode(self, data: dict) -> str:
        body = json.dumps({"d": data, "exp": int(self._clock()) + self.max_age}, separators=(",", ":"))
        payload = _b64encode(body.encode("utf-8"))
        return f"{payload}.{self._sign(payload.encode('ascii'))}"

    def decode(self, value: str) -> dict:
        """Return the session data, or {} if the cookie is missing, forged, expired or malformed."""
        payload, sep, signature = value.partition(".")
        if not sep or not payload or not signature:
            return {}
        if not hmac.compare_digest(signature, self._sign(payload.encode("ascii", "replace"))):
            return {}
        try:
            body = json.loads(_b64decode(payload))
        except (ValueError, UnicodeDecodeError):
            return {}
        if not isinstance(body, dict) or not isinstance(body.get("d"), dict):
            return {}
        if not isinstance(body.get("exp"), int) or body["exp"] < int(self._clock()):
            return {}
        return body["d"]

    def _read_cookie(self, headers: dict) -> str | None:
        raw = headers.get("cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(self.cookie_name)
        return morsel.value if morsel is not None else None

    async def __call__(self, req: Request, call_next) -> Response:
        cookie = self._read_cookie(req.headers)
        session = Session(self.decode(cookie) if cookie else {})
        req.session = session
        resp = await call_next(req)
        if session.modified and not session.to_dict():
            # Emptied (by clear or by popping every key): expire the cookie instead of keeping old data.
            return set_cookie(
                resp, self.cookie_name, "", max_age=0,
                path=self.path, secure=self.secure, httponly=self.httponly, samesite=self.samesite,
            )
        if session.modified:
            value = self.encode(session.to_dict())
            if len(value) > MAX_COOKIE_BYTES:
                raise ValueError("session data is too large for a cookie")
            return set_cookie(
                resp, self.cookie_name, value, max_age=self.max_age,
                path=self.path, secure=self.secure, httponly=self.httponly, samesite=self.samesite,
            )
        return resp
