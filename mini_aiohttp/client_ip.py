"""Real client IP behind Cloudflare.

    limiter = RateLimiter(rate=5.0, burst=15)

    app.middleware_(cloudflare_client_ip())      # first, so the limiter sees the real IP
    app.middleware_(limiter.middleware())

CF-Connecting-IP is used only when the TCP peer is inside Cloudflare's published ranges. A
request that reaches the origin directly (bypassing Cloudflare) keeps its peer address, so a
client cannot forge the header to pick its own IP. The original peer stays in
`req.peer_address`.

Cloudflare changes its ranges rarely. Check https://www.cloudflare.com/ips/ when you upgrade,
and pass `networks=` if they change before a new release.
"""
from __future__ import annotations

import ipaddress

from .security import _normalize_address

# From https://www.cloudflare.com/ips/ (IPv4 and IPv6).
CLOUDFLARE_NETWORKS = (
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "108.162.192.0/18",
    "131.0.72.0/22",
    "141.101.64.0/18",
    "162.158.0.0/15",
    "172.64.0.0/13",
    "173.245.48.0/20",
    "188.114.96.0/20",
    "190.93.240.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)


def cloudflare_client_ip(
    *,
    networks=CLOUDFLARE_NETWORKS,
    header: str = "cf-connecting-ip",
):
    """Set `req.client` to the visitor's IP from `header`, but only when the peer is trusted.

    A missing or malformed header, or a peer outside `networks`, leaves `req.client` as it was.
    """
    nets = [ipaddress.ip_network(n, strict=False) for n in networks]
    if not nets:
        raise ValueError("networks must not be empty")
    header = header.lower()

    def trusted(peer) -> bool:
        if not peer:
            return False
        try:
            address = _normalize_address(ipaddress.ip_address(peer))
        except ValueError:
            return False
        return any(address in net for net in nets)

    async def middleware(req, call_next):
        req.peer_address = req.client
        if trusted(req.client):
            claimed = req.headers.get(header, "").strip()
            try:
                req.client = str(ipaddress.ip_address(claimed))
            except ValueError:
                pass  # keep the peer address
        return await call_next(req)

    return middleware
