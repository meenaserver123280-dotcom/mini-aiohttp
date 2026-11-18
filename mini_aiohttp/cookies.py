"""Minimal cookie jar: Set-Cookie parsing, domain/path/secure matching, Max-Age expiry and deletion."""
from __future__ import annotations

import time
from urllib.parse import urlsplit


def _domain_match(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _path_match(request_path: str, cookie_path: str) -> bool:
    if request_path == cookie_path:
        return True
    if not request_path.startswith(cookie_path):
        return False
    return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"


def _default_path(request_path: str) -> str:
    if not request_path.startswith("/") or request_path.count("/") <= 1:
        return "/"
    return request_path[: request_path.rindex("/")]


class CookieJar:
    """Stores cookies from Set-Cookie headers and returns the matching Cookie header.

    Supported attributes: Domain, Path, Secure, Max-Age. Expires is ignored.
    A Domain attribute that does not match the response host is rejected.
    """

    def __init__(self, clock=time.time):
        self._clock = clock
        self._store: dict = {}  # (domain, host_only, path, name) -> (value, secure, expires_at)

    def update(self, url: str, set_cookie_values) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        default_path = _default_path(parts.path or "/")
        now = self._clock()
        for raw in set_cookie_values:
            if any(c in raw for c in "\r\n\x00"):
                continue
            pieces = [p.strip() for p in raw.split(";")]
            name, sep, value = pieces[0].partition("=")
            name = name.strip()
            if not sep or not name:
                continue

            domain, host_only, path, secure, max_age, reject = host, True, default_path, False, None, False
            for attr in pieces[1:]:
                key, _, val = attr.partition("=")
                key = key.strip().lower()
                val = val.strip()
                if key == "domain" and val:
                    candidate = val.lstrip(".").lower()
                    if not _domain_match(host, candidate):
                        reject = True
                        break
                    domain, host_only = candidate, False
                elif key == "path" and val.startswith("/"):
                    path = val
                elif key == "secure":
                    secure = True
                elif key == "max-age":
                    try:
                        max_age = int(val)
                    except ValueError:
                        max_age = None
            if reject:
                continue

            store_key = (domain, host_only, path, name)
            if max_age is not None and max_age <= 0:
                self._store.pop(store_key, None)
            else:
                expires_at = None if max_age is None else now + max_age
                self._store[store_key] = (value.strip(), secure, expires_at)

    def header_for(self, url: str) -> str | None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        path = parts.path or "/"
        https = parts.scheme == "https"
        now = self._clock()
        matches = []
        for store_key, (value, secure, expires_at) in list(self._store.items()):
            domain, host_only, cpath, name = store_key
            if expires_at is not None and expires_at <= now:
                self._store.pop(store_key, None)  # expired: drop it
                continue
            if host_only:
                if host != domain:
                    continue
            elif not _domain_match(host, domain):
                continue
            if not _path_match(path, cpath):
                continue
            if secure and not https:
                continue
            matches.append((len(cpath), name, value))
        if not matches:
            return None
        matches.sort(key=lambda m: -m[0])  # longer paths first, as browsers do
        return "; ".join(f"{name}={value}" for _, name, value in matches)
