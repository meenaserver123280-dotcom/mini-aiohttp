"""Proxy URLs for Tor/Orbot and header helpers for rotating request headers.

    session = ClientSession(proxy=tor_proxy())            # socks5h://127.0.0.1:9050
    rotator = HeaderRotator([{"User-Agent": "a"}, {"User-Agent": "b"}])
    headers = merge_headers(BASE_HEADERS, rotator.next(), {"X-Debug": "1"})
"""
from __future__ import annotations

import random
from urllib.parse import quote

from .response import check_header

TOR_SOCKS_HOST = "127.0.0.1"
# Tor's default SOCKS port. Orbot on Android also listens here by default.
TOR_SOCKS_PORT = 9050


def socks5_url(host: str, port: int, *, username=None, password=None, remote_dns: bool = True) -> str:
    """Build a SOCKS5 proxy URL. `remote_dns=True` gives socks5h, so the proxy resolves names."""
    if not host:
        raise ValueError("proxy host is required")
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("proxy port must be between 1 and 65535")
    scheme = "socks5h" if remote_dns else "socks5"
    auth = ""
    if username is not None:
        auth = quote(username, safe="") + ":" + quote(password or "", safe="") + "@"
    return f"{scheme}://{auth}{host}:{port}"


def tor_proxy(host: str = TOR_SOCKS_HOST, port: int = TOR_SOCKS_PORT) -> str:
    """Proxy URL for a local Tor daemon or Orbot. Uses socks5h so DNS goes through Tor."""
    return socks5_url(host, port, remote_dns=True)


def merge_headers(*sources) -> dict:
    """Merge header dicts left to right.

    A later header replaces an earlier one with the same name, whatever the case.
    The spelling of the last value is kept. Values are checked for header injection.
    """
    result: dict = {}
    spelled: dict = {}  # lowercase name -> key currently in result
    for source in sources:
        for name, value in (source or {}).items():
            check_header(name, value)
            lowered = name.lower()
            if lowered in spelled:
                del result[spelled[lowered]]
            result[name] = str(value)
            spelled[lowered] = name
    return result


class HeaderRotator:
    """Cycle through header presets, for example different User-Agent strings.

    `next()` returns a copy, so callers can change it safely. Use `shuffle=True` for a
    random choice each time instead of round robin.
    """

    def __init__(self, presets, *, shuffle: bool = False, seed=None):
        presets = [dict(p) for p in presets]
        if not presets:
            raise ValueError("at least one header preset is required")
        for preset in presets:
            merge_headers(preset)  # validates names and values
        self._presets = presets
        self._shuffle = shuffle
        self._rng = random.Random(seed)
        self._position = 0

    def next(self) -> dict:
        if self._shuffle:
            preset = self._rng.choice(self._presets)
        else:
            preset = self._presets[self._position % len(self._presets)]
            self._position += 1
        return dict(preset)
