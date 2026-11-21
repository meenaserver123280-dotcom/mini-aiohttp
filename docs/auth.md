# Authentication and CSRF

## Client IP

`req.client` is the TCP peer address of the connection. Headers such as `X-Forwarded-For` are
not read, so a client cannot change the IP that the rate limiter sees. The trade-off: behind a
reverse proxy (Nginx, Cloudflare, a load balancer), `req.client` is the proxy's address, so all
clients share one limit. Handle that at the proxy level, or tell us if you need trusted-proxy
support.

## CSRF protection

```python
from mini_aiohttp import Response, Server, csrf_protect

app = Server()
app.middleware_(csrf_protect(secret=os.environ["CSRF_SECRET"]))   # at least 32 bytes

@app.route("/form")
async def form(req):
    return Response(200, f'<form method="post" action="/save">'
                         f'<input type="hidden" name="csrf_token" value="{req.csrf_token}">'
                         f'<button>Save</button></form>')
```

Every visitor gets a signed token in an HttpOnly, SameSite=Lax cookie. POST, PUT, PATCH and DELETE
requests must send the same token in the `X-CSRF-Token` header or in a form field named
`csrf_token`, otherwise they get 403. Form fields are read only for `application/x-www-form-urlencoded`
bodies. For multipart forms, send the token in the header.

## Basic auth

```python
from mini_aiohttp import basic_auth, route_middleware

@app.route("/admin")
@route_middleware(basic_auth({"admin": os.environ["ADMIN_PASSWORD"]}, realm="Admin"))
async def admin(req):
    return Response.json({"user": req.user})
```

Failures get 401 with `WWW-Authenticate: Basic realm=...`. Pass `verify=fn(user, password)` to
check against a database instead of a dict. Basic auth is only safe over HTTPS.

## API keys

```python
from mini_aiohttp import api_key_auth, route_middleware

@app.route("/api/items")
@route_middleware(api_key_auth(os.environ["API_KEYS"].split(",")))
async def items(req):
    ...
```

Keys are read from the `X-API-Key` header only (not the URL, because URLs get logged). Pass
`header=` to change the name, or `verify=fn(key)` to check against a database. Keys are compared as
SHA-256 digests in constant time.
