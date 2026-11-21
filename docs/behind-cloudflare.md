# Running behind Cloudflare

When Cloudflare sits in front of your app, `req.client` is the Cloudflare edge address, not the
visitor's. Without a fix, every visitor shares one rate-limit bucket.

```python
from mini_aiohttp import RateLimiter, Server, cloudflare_client_ip

app = Server()
app.middleware_(cloudflare_client_ip())   # must come first
app.middleware_(RateLimiter(rate=5.0, burst=15).middleware())
```

## How it decides

- `CF-Connecting-IP` is read only when the TCP peer is inside Cloudflare's published ranges
  (`CLOUDFLARE_NETWORKS`, from https://www.cloudflare.com/ips/).
- If a request reaches your server directly, it keeps its own address, so a client cannot fake the
  header to pick an IP.
- A missing or malformed header leaves `req.client` unchanged.
- The original peer is kept in `req.peer_address`.

## Keep it safe

- Lock your origin to Cloudflare. Allow inbound traffic only from Cloudflare's ranges at the firewall.
  Then the trust check is backed by the network too.
- Cloudflare changes its ranges occasionally. Update `CLOUDFLARE_NETWORKS` in a release, or pass
  `networks=` with the current list.
