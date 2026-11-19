# ⚡ mini-aiohttp

**mini-aiohttp v1.0.0** is a lightweight, asynchronous HTTP/1.1 client and server framework built with Python and asyncio, with optional HTTP/2, HTTP/3, WebSocket and ASGI support.

Designed for simplicity, performance, flexibility and security. The core has no required dependencies and keeps pure-Python fallbacks. Prebuilt wheels include an optional PyO3 extension that accelerates HTTP request-head parsing and buffered response serialization.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/Version-1.0.0-green)](https://pypi.org/project/mini-aiohttp/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](https://opensource.org/license/mit)

---

## ✨ Features

### HTTP Client
- Asynchronous HTTP/1.1 requests.
- Connection pooling and keep-alive.
- Per-host concurrency limits.
- Automatic redirect handling.
- Bounded retries for idempotent methods, and a `RetryPolicy` helper for 429 and 502/503/504.
- Cookie management, including Max-Age expiry.
- Automatic decoding of `gzip`, `deflate` and (with an extra) `br` responses.
- Streaming response downloads.
- Async streaming uploads.
- Multipart form and file uploads.
- HTTP and SOCKS5/SOCKS5h proxies, including HTTPS CONNECT/tunnelling, and a Tor/Orbot preset.
- DNS caching.
- Configurable connection and request timeouts.
- TLS context customization, and optional certifi CA bundle.
- Request and response hooks.
- Response size limits.
- JSON parsing on responses (`response.json()`).

- Standalone WebSocket client and server (`ws://` / `wss://`).
- Automatic HTTPS client: ALPN selects HTTP/2 when available, otherwise HTTP/1.1; Alt-Svc enables HTTP/3 on subsequent requests (`AutoClientSession`).
- Optional HTTP/2 client/server adapters using ALPN, with pooled connections and parallel streams (`pip install mini-aiohttp[http2]`).
- Optional HTTP/3 client/server adapters using QUIC, with pooled connections and parallel streams (`pip install mini-aiohttp[http3]`).
- Telegram Bot API polling (`TelegramPoller`).

### HTTP Server
- Asynchronous HTTP server with optional Rust-accelerated request parsing and response serialization (pure-Python fallbacks supported).
- Custom routing and path parameters.
- Middleware support, including a traffic recorder for inspecting recent requests.
- WebSocket endpoints on the HTTP port (`Server.websocket`).
- Webhook receiver with secret token check, update deduplication and background dispatch (`WebhookRouter`).
- ASGI 3 adapter (`ASGIApp`) for uvicorn, hypercorn and other ASGI servers.
- Gzip response compression middleware (`gzip_middleware`).
- HTTP/1.1 persistent connections.
- Chunked request-body decoding.
- Chunked streaming responses.
- HTTPS support using SSLContext.
- Static file serving with HTTP `Range` (206/416), `ETag`, `Last-Modified` and conditional GET (`StaticFiles`).
- Signed-cookie sessions (`SessionMiddleware`).
- Form body parsing with size limits (`parse_urlencoded`).
- Minimal template engine with HTML escaping (`Template`, `TemplateLoader`).
- Path traversal protection.
- Graceful server shutdown.
- Connection limits.
- Runtime server statistics.

### Security
- HTTP header validation.
- Request framing validation.
- Content-Length and Transfer-Encoding conflict protection.
- CORS middleware.
- Security headers.
- Token-bucket rate limiting, and a Redis-backed limiter for multiple servers.
- IP/CIDR policies.
- Secure cookie helpers.
- Tamper-evident signed session cookies.
- JWT bearer authentication middleware (extra).
- JSON request validation, plain or with pydantic models (extra).
- Bounded request and response parsing, fuzz-tested with hypothesis.

### Observability (extras)
- Prometheus metrics middleware and `/metrics` handler.
- OpenTelemetry server spans.

---

## 📦 Installation

Core (no required dependencies):

```bash
pip install mini-aiohttp
```

Requires Python 3.11 or newer.

Prebuilt wheels are published for Linux x86_64, macOS (x86_64 and arm64) and Windows x86_64, on Python 3.11, 3.12 and 3.13. These wheels include the native Rust extension (`mini_aiohttp._native`). On any other platform, pip builds from the source distribution. If no Rust toolchain is available, the build still succeeds and the package uses its pure-Python parser and response serializer. Nothing in the API changes between the two.

Optional extras:

| Extra | What it adds |
|---|---|
| `http2` | HTTP/2 client and server (`h2`) |
| `http3` | HTTP/3 client and server (`aioquic`) |
| `brotli` | `br` Content-Encoding support |
| `orjson` | Faster `Response.json` / `Request.json` |
| `certifi` | Mozilla CA bundle for HTTPS |
| `speed` | `orjson` + `uvloop` (use with `use_uvloop()`) |
| `prometheus` | Prometheus metrics middleware |
| `jwt` | JWT bearer auth middleware |
| `pydantic` | Pydantic-validated JSON bodies |
| `otel` | OpenTelemetry tracing middleware |
| `all` | Every runtime extra above |

```bash
pip install "mini-aiohttp[http2,http3]"
pip install "mini-aiohttp[brotli,orjson,certifi]"
pip install "mini-aiohttp[prometheus,jwt,pydantic,otel]"
pip install "mini-aiohttp[all]"
```

Install a specific version:

```bash
pip install mini-aiohttp==1.0.0
```

Upgrade an existing installation:

```bash
pip install --upgrade mini-aiohttp
```

Verify installation:

```bash
pip show mini-aiohttp
```

---

## 🚀 Quick Start

### Create an HTTP Server

```python
import asyncio
from mini_aiohttp import Server, Response, gzip_middleware

app = Server()
app.middleware_(gzip_middleware(min_size=1024))

@app.route("/")
async def home(req):
    return Response.json({
        "message": "Welcome to mini-aiohttp",
        "version": "1.0.0"
    })

@app.route("/hello/{name}")
async def hello(req):
    return Response.json({
        "hello": req.params["name"]
    })

async def main():
    await app.start("127.0.0.1", 8080)

    try:
        await asyncio.Event().wait()
    finally:
        await app.close()

asyncio.run(main())
```

### WebSocket on the same port

```python
from mini_aiohttp import Server

app = Server()

@app.websocket("/ws/{room}")
async def chat(ws, req):
    await ws.send(f"joined {req.params['room']}")
    while True:
        msg = await ws.recv()
        if msg is None:
            break
        await ws.send(msg)
```

Clients connect to `ws://127.0.0.1:8080/ws/lobby`. The HTTP routes and the WebSocket route share one port.

### Run under uvicorn (ASGI)

```python
# myapp.py
from mini_aiohttp import ASGIApp, Response, Server

server = Server()

@server.route("/hello/{name}")
async def hello(req):
    return Response.json({"hello": req.params["name"]})

asgi_app = ASGIApp(server)
```

```bash
pip install uvicorn
uvicorn myapp:asgi_app --host 127.0.0.1 --port 8000
```

### HTTP Client

```python
import asyncio
from mini_aiohttp import ClientSession

async def main():
    async with ClientSession() as session:
        response = await session.get(
            "https://example.com"
        )

        print("Status:", response.status)
        print("Body:", response.text())

asyncio.run(main())
```

### Faster event loop (optional)

```python
import asyncio
from mini_aiohttp import use_uvloop

use_uvloop()  # needs the 'speed' extra; returns False if uvloop is missing
asyncio.run(main())
```

### Static files with Range and ETag

```python
from mini_aiohttp import Server
from mini_aiohttp.static import StaticFiles

app = Server()
app.middleware_(StaticFiles("/static", "public"))
# GET /static/video.mp4 with "Range: bytes=0-1023" returns 206 with Content-Range.
# GET /static/app.css with "If-None-Match: <etag>" returns 304 with no body.
```

### Sessions and forms

```python
import os
from mini_aiohttp import Response, Server, SessionMiddleware
from mini_aiohttp.forms import FormError, parse_urlencoded

app = Server()
app.middleware_(SessionMiddleware(secret=os.environ["SESSION_SECRET"]))  # 32+ bytes

@app.route("/login", method="POST")
async def login(req):
    try:
        fields = parse_urlencoded(req)
    except FormError as exc:
        return Response(exc.status, str(exc))
    req.session["user"] = fields.get("user", [""])[0]
    return Response(303, b"", {"Location": "/"})

@app.route("/me")
async def me(req):
    return Response.json({"user": req.session.get("user")})
```

### Templates

```python
from mini_aiohttp import Server, TemplateLoader, html_response

loader = TemplateLoader("templates")  # templates/home.html
app = Server()

@app.route("/")
async def home(req):
    return html_response(loader.render("home.html", title="Hi", user={"name": "Ada"}))
```

### Prometheus metrics (optional)

```python
from mini_aiohttp.extras.metrics import prometheus_middleware, metrics_handler

app.middleware_(prometheus_middleware())
app.route("/metrics")(metrics_handler())
```

### JWT auth (optional)

```python
from mini_aiohttp.extras.jwt_auth import jwt_auth

app.middleware_(jwt_auth(secret="change-me", audience="my-api", exempt=("/health",)))

@app.route("/me")
async def me(req):
    return Response.json(req.user)   # decoded claims
```

### Pydantic-validated JSON (optional)

```python
from pydantic import BaseModel
from mini_aiohttp.extras.pydantic_json import validated_json

class User(BaseModel):
    name: str
    age: int

@app.route("/users", method="POST")
@validated_json(User)
async def create(req):
    return Response.json({"ok": True, "name": req.model.name})
```

### Rate limiting across servers (optional)

```python
import redis.asyncio as redis
from mini_aiohttp.extras.redis_limiter import RedisRateLimiter

limiter = RedisRateLimiter(redis.Redis(), rate=20)  # 20 requests/second per client
app.middleware_(limiter.middleware())
```

---

## 📖 API Reference

Everything below is exported from the top-level package (`from mini_aiohttp import ...`) unless a module path is given. `mini_aiohttp.__version__` holds the package version string.

### Server

```python
Server(*, max_connections=100_000, read_timeout=15.0, max_body_size=10 * 1024 * 1024, backlog=4096)
```

| Method or property | Purpose |
|---|---|
| `route(path, method="GET")` | Decorator that registers a handler. Use `{name}` for path parameters, available as `req.params`. Literal routes take priority over parameterized ones. The first registration of the same method and path wins. |
| `websocket(path)` | Decorator that registers a WebSocket endpoint on the HTTP port. The handler is `async def handler(ws, req)`. See the WebSocket endpoints section below. |
| `middleware_(fn)` | Registers `async def fn(req, call_next)`. Middleware runs for every HTTP request, including 404, 405 and static files. It does not run for WebSocket connections. |
| `static(prefix, directory, *, max_file_size=50 * 1024 * 1024)` | Serves files under `prefix` with the built-in static handler (GET and HEAD only, no Range). Paths must stay inside `directory`, so `..` and symlinks cannot escape it. Files are streamed in 64 KiB chunks. Larger files get 413. For Range and ETag support, use `StaticFiles` (below). |
| `start(host="127.0.0.1", port=8080, *, reuse_port=False, ssl=None)` | Starts listening. Pass an `ssl.SSLContext` (server side) to serve HTTPS. Returns the asyncio server. |
| `serve_forever(host="127.0.0.1", port=8080, **kwargs)` | Starts and runs until cancelled. |
| `close(*, grace=5.0)` | Stops accepting connections, lets in-flight requests finish for up to `grace` seconds, then closes the rest. Does not raise on slow shutdown. |
| `port` | Bound port (useful with `port=0`). |
| `stats` | Snapshot of counters, see Server Statistics below. |

Handlers may return a `Response`, or a `str` or `bytes` value, which is sent as a 200 response.

HEAD requests are answered by the matching GET route (the body is dropped and `Content-Length` reports the GET body length). Static files also answer HEAD.

**Server responses and limits:**

| Situation | Result |
|---|---|
| No route for the path | 404 |
| Path exists, method does not | 405 |
| Handler raises an exception | 500 (logged with traceback) |
| Request line or header line longer than 8 KiB | 431 |
| Request head larger than 256 KiB, or more than 100 headers | 431 |
| Body larger than `max_body_size` | 413 |
| Duplicate `Host`, `Content-Length` or `Transfer-Encoding` header | 400 |
| `Content-Length` and `Transfer-Encoding` together | 400 |
| Transfer-Encoding other than exactly `chunked`, or non-HTTP/1.1 chunked body | 501 |
| HTTP version other than 1.0 or 1.1 | 505 |
| More than `max_connections` open, or server draining | 503 |
| Idle or slow client longer than `read_timeout` | Connection closed |

HTTP/1.1 connections are kept alive unless the client sends `Connection: close`. HTTP/1.0 connections are kept alive only with `Connection: keep-alive`. `Expect: 100-continue` is answered.

### Request

Passed to every handler and middleware.

| Attribute or method | Description |
|---|---|
| `method`, `path`, `query`, `version` | Request line parts. `query` is the raw query string. |
| `headers` | Dict with lowercase header names. |
| `body` | Request body as bytes (decoded from chunked encoding if needed). |
| `params` | Path parameters from `{name}` segments. |
| `client` | Peer IP address as a string, or `None`. |
| `text()` | Body decoded as UTF-8. |
| `json()` | Body parsed as JSON. Raises `ValueError` on invalid input. |
| `query_params()` | Query string as `{name: [values]}`, blanks kept. |
| `form()` | Parsed `multipart/form-data` body as `{name: [str or UploadedFile, ...]}`. Raises `ValueError` on bad bodies, so catch it and return 400. |
| `session` | Set by `SessionMiddleware`. A dict-like `Session`. |
| `user`, `model` | Set by the `jwt_auth` and `validated_json` extras. |

The server does not parse cookies. Read them from `req.headers["cookie"]`, or use `SessionMiddleware`.

### Response

```python
Response(status=200, body=b"", headers=None)
```

- `body` may be `str` (encoded as UTF-8) or bytes. `headers` names are stored lowercase.
- When not set, `content-type` defaults to `text/plain; charset=utf-8`. `content-length` and `connection` are added automatically.
- `Response.json(data, status=200)`: JSON body with `application/json; charset=utf-8`.
- `Response.stream(chunks, status=200, headers=None)`: sends an async iterable of bytes or str as a chunked response, without buffering it all in memory. Streamed bodies are closed if the connection drops.
- `add_set_cookie(raw)`: queues a `Set-Cookie` header. Use the `set_cookie` helper for safe defaults.
- `text(encoding="utf-8")`: body as text, for buffered responses.
- `reason`, `is_streaming`: status reason phrase and streaming flag.

On the client side, a `Response` also has `status`, `headers` (lowercase names), `body` (bytes), `set_cookies` (raw `Set-Cookie` values) and these methods for streamed bodies: `iter_chunks()`, `aread()` and `aclose()`. `text()` on a streamed response raises `RuntimeError` until `aread()` has been called. `response.json()` parses the body (see Parsing responses below).

### Static files: `StaticFiles`

```python
from mini_aiohttp.static import StaticFiles

StaticFiles(prefix, directory, *, max_file_size=50 * 1024 * 1024, cache_control="public, max-age=3600")
```

Register it as middleware: `app.middleware_(StaticFiles("/static", "public"))`. Requests under the prefix are answered by it. All other requests go to the next middleware and route.

| Feature | Behaviour |
|---|---|
| Methods | GET and HEAD. Other methods get 405 with `Allow: GET, HEAD`. |
| Missing file | 404. Paths that escape `directory`, including encoded `..`, are 404. |
| Range | One byte range per request: `bytes=a-b`, `bytes=a-`, or suffix `bytes=-n`. Returns 206 with `Content-Range`. An unsatisfiable range returns 416 with `Content-Range: bytes */size`. A malformed or multi-range header is ignored and the full file is sent. |
| `Accept-Ranges` | `bytes` on every response. |
| `ETag` | Built from size and modification time. Weak comparison is used. |
| `Last-Modified` | Set on every response. |
| Conditional GET | `If-None-Match` (including `*`) and `If-Modified-Since` return 304 with no body. |
| `If-Range` | When the validator matches, the partial range is kept. When it does not, the full file is sent. |
| Size limit | Files over `max_file_size` get 413. |
| Content-Type | Guessed from the file name. Unknown types are `application/octet-stream`. |

Files are read in 64 KiB chunks in a worker thread, so the event loop is not blocked by disk reads.

### Sessions: `SessionMiddleware`

```python
from mini_aiohttp import SessionMiddleware, Session

SessionMiddleware(
    secret, *, cookie_name="session", max_age=3600, path="/",
    secure=True, httponly=True, samesite="Lax",
)
```

- `secret` must be at least 32 bytes. Keep it in an environment variable or secret store.
- The session data is stored in the cookie itself, as base64url JSON signed with HMAC-SHA256. The server keeps no session state, so sessions work across multiple processes that share the secret.
- The signature prevents tampering. It does not hide the data. Do not store secrets in a session.
- A missing, forged, expired or malformed cookie gives an empty session. Expiry is checked on every request.
- `req.session` supports `[]`, `get`, `pop`, `in`, `del`, and `clear()`. Changes set a new cookie. An unchanged session sends no cookie.
- Clearing the session, or popping every key, expires the cookie (`Max-Age=0`).
- Data must fit in about 3.8 KiB. A larger session raises `ValueError`.
- Set `secure=False` only for local development over plain HTTP. Logout is `req.session.clear()`. Stolen cookies stay valid until they expire, so keep `max_age` short for sensitive apps.

### Forms: `parse_urlencoded`

```python
from mini_aiohttp.forms import parse_urlencoded, FormError

parse_urlencoded(req, *, max_bytes=64 * 1024, max_fields=100) -> dict[str, list[str]]
```

Parses `application/x-www-form-urlencoded` bodies, the format of plain HTML forms. Blank values are kept, and repeated names give several values. The `Content-Type` parameters are ignored, and the media type is matched case-insensitively.

`FormError` (a `ValueError`) has a `status` attribute to return:

| Status | Cause |
|---|---|
| 415 | Content-Type is not `application/x-www-form-urlencoded` (or is missing). |
| 413 | Body is larger than `max_bytes`. |
| 400 | Too many fields, or the body is not valid ASCII form encoding. |

For `multipart/form-data` uploads, use `req.form()` instead.

### Templates: `Template`, `TemplateLoader`, `html_response`

```python
from mini_aiohttp import Template, TemplateLoader, TemplateError, html_response
```

Syntax:

| Syntax | Meaning |
|---|---|
| `{{ name }}` | Value, HTML-escaped (`&`, `<`, `>`, `"`, `'`). |
| `{{! name }}` | Value inserted raw. Use only for trusted markup. |
| `{{ user.name }}` | Dotted lookup. Works on dicts and on object attributes. |

Anything that does not match these forms is kept as literal text. There are no loops or conditions. Keep logic in Python and pass ready values to the template.

- `Template(source)`: compile a string once. `render(**context)` returns a string.
- `TemplateLoader(directory)`: loads files from a directory. Names cannot leave the directory. Files are cached and reloaded when their modification time changes. `get(name)` returns a `Template`, and `render(name, **context)` renders directly.
- A missing template, or a variable missing from the context, raises `TemplateError`. Typos fail loudly.
- `html_response(body, status=200)`: a UTF-8 HTML `Response`.

### ASGI adapter

```python
from mini_aiohttp import ASGIApp

ASGIApp(server, *, on_startup=None, on_shutdown=None)
```

`ASGIApp` wraps a `Server` so that any ASGI 3 server can run it: uvicorn, hypercorn and similar. The adapter does no routing of its own. Routes, middleware, static files and the 404, 405 and 500 responses all come from `Server`.

| Area | Behaviour |
|---|---|
| HTTP requests | Go through the same dispatch code as the built-in server, so middleware runs for every request. |
| Path | Uses the ASGI `raw_path` when provided, so percent-escapes are decoded once, as on the built-in server. |
| Request body | Larger than `max_body_size` gets 413 before routing, as on the built-in server. |
| HEAD | The body is dropped and `Content-Length` is kept. |
| Streaming | `Response.stream` chunks are passed to the ASGI server, which does the chunked framing. |
| Framing headers | Your `Content-Length` and `Transfer-Encoding` headers are ignored, because the ASGI server sets framing. |
| Client disconnect | No response is sent. |
| Lifespan | `on_startup` and `on_shutdown` run once each. If one raises, the server receives a `lifespan.*.failed` message. |
| WebSocket | Refused with a close before accept. Use `Server.websocket` on the built-in server, or `WebSocketServer` on its own port. |
| Limits | Request-line length, header count, idle timeout and connection cap are enforced by the ASGI server, not by `Server`. `max_body_size` still applies. |
| Statistics | Requests served through `ASGIApp` are counted in `server.stats`. |

Example:

```python
from mini_aiohttp import ASGIApp, Response, Server

server = Server()

@server.route("/users/{user_id}")
async def user(req):
    return Response.json({"id": req.params["user_id"]})

async def open_resources():
    ...  # e.g. create a database pool

async def close_resources():
    ...  # e.g. close it

asgi_app = ASGIApp(server, on_startup=open_resources, on_shutdown=close_resources)
```

### ClientSession

```python
ClientSession(
    *, timeout=10.0, connect_timeout=5.0, max_per_host=1000, max_idle_per_host=100,
    max_response_size=1 GiB, max_redirects=10, retries=0,
    retry_statuses=(429, 502, 503, 504), backoff=0.5, cookies=True,
    ssl_context=None, proxy=None, dns_cache_ttl=300.0,
    on_request=(), on_response=(), user_agent="mini-aiohttp",
)
```

Use it as an async context manager, or call `close()` yourself.

| Method | Description |
|---|---|
| `get(url, **kwargs)` | Shortcut for `request("GET", url, ...)`. |
| `post(url, **kwargs)` | Shortcut for `request("POST", url, ...)`. |
| `request(method, url, *, headers=None, data=None, json=None, form=None, files=None, timeout=None, stream=False, allow_redirects=True)` | Sends one request and returns a `Response`. |
| `close()` | Closes pooled connections. |

**`request` arguments:**
- `data`: `str` or bytes body, or an async iterable of chunks. An async iterable is sent with `Transfer-Encoding: chunked`.
- `json`: any JSON-serializable value. Sets `Content-Type: application/json` unless you set one.
- `form`: dict of fields, sent as `multipart/form-data`.
- `files`: dict mapping a field name to `(filename, bytes, content_type)`, sent with `form`.
- `json` cannot be combined with `form` or `files`. A streaming `data` body cannot be combined with `json`, `form` or `files`.
- `timeout`: overrides the session timeout for this request. It covers sending the request and reading the response headers, and for non-streamed responses the whole body.
- `stream=True`: reads the body lazily with `iter_chunks()`, `aread()` or `aclose()`.
- `allow_redirects=False`: returns the 3xx response instead of following it.

**Defaults and behaviour:**
- Default headers: `Host`, `User-Agent`, `Accept: */*`, and `Accept-Encoding: gzip, deflate` (plus `br` when the brotli extra is installed). A caller header with the same name, in any case, replaces the default.
- You cannot set `Content-Length` or `Transfer-Encoding` yourself. The client raises `ValueError`.
- Retries (`retries`) apply only to GET, HEAD, PUT, DELETE, OPTIONS and TRACE, and never to streaming bodies. POST and PATCH are never retried. Backoff is exponential, capped at 10 seconds. A numeric `Retry-After` is honoured up to 60 seconds. For more control, use `RetryPolicy` (below).
- Redirects (301, 302, 303, 307, 308) are followed up to `max_redirects`. 303 becomes GET. 301 and 302 become GET when sent as POST. A streaming body cannot be replayed for 307 or 308. `Authorization` and `Proxy-Authorization` are removed when the redirect goes to another origin. Only http and https targets are followed.
- Response bodies are decoded for `gzip`, `deflate` and `br`. After decoding, `content-encoding` and `content-length` are removed from `headers`.
- Bodies larger than `max_response_size` raise `ClientError`. Pass `None` to disable the limit.
- Response headers: more than 200 headers, or a line longer than 64 KiB, raise `ClientError`.
- Cookies are stored in `session.cookie_jar` (`None` when `cookies=False`). A caller-supplied `Cookie` header turns off automatic cookies for that request.
- `on_request` hooks are called as `hook(method, url, headers)`, and may be async. `on_response` hooks are called as `hook(response)`, and may be async.
- Connection pooling is per host, port and scheme. The DNS cache uses `dns_cache_ttl` seconds (0 disables it).

### AutoClientSession

```python
AutoClientSession(*, timeout=10.0, ssl_context=None, **client_options)
```

A session for mixed HTTP/1.1, HTTP/2 and HTTP/3 traffic.
- For https URLs, the first request uses HTTP/2 when ALPN negotiates it. Otherwise it uses HTTP/1.1.
- Responses with an `Alt-Svc: h3=...` header switch later requests to that origin over HTTP/3. A failed HTTP/3 request falls back to the other protocols.
- HTTP/2 is used only when `ssl_context` is not given. With a custom context, requests use HTTP/1.1, and HTTP/3 if Alt-Svc advertises it.
- Methods: `request(method, url, *, headers=None, data=b"")`, `get(url, **kwargs)`, `post(url, **kwargs)`, `close()`. It supports async context management. It always returns a buffered `Response`, so it has no `stream` option.
- Other keyword arguments go to `ClientSession` for the HTTP/1.1 path.

### Batch requests

```python
from mini_aiohttp import fetch_all, gather_requests
```

- `fetch_all(requests, *, concurrency=100, timeout=10.0, return_exceptions=True)`: opens a session, runs all requests, and closes it.
- `gather_requests(session, requests, *, concurrency=100, return_exceptions=True)`: runs requests on an existing session.

Each item in `requests` can be a URL string (GET), a `(method, url)` or `(method, url, kwargs)` tuple, or a dict such as `{"method": "POST", "url": ..., "json": ...}`. Results come back in input order. By default a failed request is returned as its exception object, and it does not cancel the others. `concurrency` must be an integer of at least 1.

```python
results = await fetch_all([
    "https://example.com/a",
    ("POST", "https://example.com/b", {"json": {"x": 1}}),
    {"method": "GET", "url": "https://example.com/c"},
])
```

### Retry policy: `RetryPolicy`

```python
from mini_aiohttp import ClientSession, RetryPolicy

policy = RetryPolicy(max_retries=3, base_delay=0.25, max_delay=10.0)
async with ClientSession() as session:
    response = await policy.send(session, "GET", "https://example.com/api")
```

- 429 is retried for every method, because the server rejected the request before processing it.
- 502, 503 and 504 are retried only for GET, HEAD, PUT, DELETE, OPTIONS and TRACE.
- A numeric `Retry-After` header is honoured, capped at `retry_after_cap` (60 seconds). Otherwise the delay is exponential backoff with full jitter, capped at `max_delay`. An HTTP-date `Retry-After` falls back to backoff.
- Connection errors and timeouts are retried for idempotent methods only. Async-iterable request bodies are never replayed.
- When the budget runs out, `send` returns the last response (or raises the last connection error).

### Proxies and headers

```python
from mini_aiohttp import ClientSession, HeaderRotator, merge_headers, socks5_url, tor_proxy
```

- `tor_proxy(host="127.0.0.1", port=9050)` returns `socks5h://127.0.0.1:9050`, the Tor and Orbot default. Use it as `ClientSession(proxy=tor_proxy())`. The `socks5h` scheme sends DNS lookups through the proxy.
- `socks5_url(host, port, *, username=None, password=None, remote_dns=True)` builds a SOCKS5 URL and percent-encodes the credentials.
- `merge_headers(*sources)` merges header dicts. A later name replaces an earlier one, ignoring case. Values are checked for header injection.
- `HeaderRotator(presets, *, shuffle=False, seed=None)`: `next()` returns a copy of the next preset, round robin, or a random one with `shuffle=True`.

```python
rotator = HeaderRotator([{"User-Agent": "agent-a"}, {"User-Agent": "agent-b"}])
headers = merge_headers({"Accept": "application/json"}, rotator.next(), {"X-Debug": "1"})
```

### Webhooks: `WebhookRouter`

```python
from mini_aiohttp import Server, WebhookRouter

async def on_update(update: dict) -> None:
    ...

app = Server()
router = WebhookRouter(on_update, secret_token=os.environ["WEBHOOK_SECRET"])
router.attach(app, "/webhook")
# on shutdown:
await router.close()
```

- The `X-Telegram-Bot-Api-Secret-Token` header is checked in constant time. A missing or wrong token gets 403. Pass `secret_header=` for other providers.
- Invalid JSON, or a body that is not a JSON object, gets 400.
- A valid update gets 200 `{"ok": true}` at once. The handler runs in a background task.
- Recent `update_id` values are remembered for `dedupe_ttl` seconds (default 1 hour, up to `dedupe_size` entries). A repeat gets 200 with `"duplicate": true` and is not dispatched again.
- Handler errors are logged and never reach the client. At most `max_tasks` handlers run at once. Beyond that the webhook answers 503 and Telegram retries later.

### Telegram polling: `TelegramPoller`

```python
from mini_aiohttp import ClientSession, TelegramPoller

async with ClientSession() as session:
    poller = TelegramPoller(session, token, on_update, timeout=25)
    await poller.run()   # call poller.stop() to end the loop
```

- Calls `getUpdates` with an offset, so each update is handled once. The offset moves past an update before its handler runs.
- Network errors and Telegram errors are logged by class name, and polling continues after a short backoff.
- The token appears only in the request URL and is never logged.
- Do not poll and use a webhook for the same bot at the same time.

### Traffic recorder: `TrafficRecorderMiddleware`

```python
from mini_aiohttp import Server, TrafficRecorderMiddleware

recorder = TrafficRecorderMiddleware(maxlen=500, body_preview=200)
app = Server()
app.middleware_(recorder)

for entry in recorder.records:
    print(entry["method"], entry["path"], entry["status"], entry["duration_ms"])
```

- Each record has `method`, `path`, `status`, `duration_ms`, `client` and `body_preview`. The list is oldest first.
- `body_preview` is 0 bytes by default. Request bodies can contain passwords and tokens, so turn it on only while debugging.
- A handler that raises is recorded as status 500. `clear()` empties the buffer.

### Parsing responses: `response.json()`

```python
response = await session.get("https://api.example.com/items")
items = response.json()
```

`response.json()` parses the body of a response you received. `Response.json(data, status=200)` still builds a JSON response on the server, and the same name works both ways. Parsing a streamed body needs `await response.aread()` first.

### Cookies

```python
from mini_aiohttp import CookieJar
```

`CookieJar` stores `Set-Cookie` values and returns the matching `Cookie` header. It supports the Domain, Path, Secure and Max-Age attributes. `Expires` is ignored. A Domain attribute that does not match the response host is rejected. Host-only cookies are the default. Expired cookies are removed. `update(url, set_cookie_values)` and `header_for(url)` are the main methods. `ClientSession` creates one automatically (`cookies=True`).

### WebSocket endpoints on the HTTP port

```python
app = Server()

@app.websocket("/ws/{room}")
async def chat(ws, req):
    ...
```

`Server.websocket(path)` registers a WebSocket endpoint that is served on the same port as the HTTP routes. A client sends a GET request with `Upgrade: websocket`. If the path matches a registered endpoint, the server completes the RFC 6455 handshake and calls your handler with the connection and the request.

- `ws` is a `WebSocket`. Use `send(data)`, `recv()` and `close(code, reason)` as described in the WebSocket section below.
- `req.params` holds the path parameters, such as `room` in the example above.
- `req.headers` holds the handshake headers, including `origin` and `cookie`.

| Behaviour | Detail |
|---|---|
| Matching | A GET with `Upgrade: websocket` on a registered path. Path parameters use the same `{name}` syntax as `route`. |
| Other paths | An upgrade request to an unregistered path is handled as normal HTTP, so it gets 404 or 405. |
| Bad handshake | 400, with the reason in the body. |
| Middleware | Not run for WebSocket connections. |
| Origin | Not checked. Check `req.headers["origin"]` in the handler, especially when you authenticate with cookies. |
| Normal close | When the handler returns, the server sends close code 1000. |
| Handler error | An unhandled exception is logged and the connection is closed with code 1011. |
| Limits | Frames and messages are limited to 16 MiB. Each WebSocket connection counts toward `max_connections`. |
| Shutdown | `close()` waits up to `grace` seconds for open connections, then closes them. |

Because middleware does not run, put authentication inside the handler:

```python
ALLOWED_ORIGINS = {"https://example.com"}

@app.websocket("/live")
async def live(ws, req):
    if req.headers.get("origin") not in ALLOWED_ORIGINS:
        await ws.close(1008, "origin not allowed")
        return
    # ... your checks, then the message loop ...
```

### WebSocket (standalone server and client)

```python
from mini_aiohttp import WebSocket, WebSocketServer, WebSocketError
```

Use the standalone server when the WebSocket endpoints need their own port, or when the handler needs the full HTTP headers. It is independent of `Server`.

**Client:**

```python
WebSocket.connect(url, *, headers=None, ssl_context=None, timeout=10.0)  # ws:// or wss://
```

- `send(data)`: `str` is sent as a text message, bytes-like as a binary message.
- `recv()`: returns a `str` for text, `bytes` for binary, or `None` after a close frame. Pings are answered automatically.
- `close(code=1000, reason="")`: sends a close frame. `reason` is at most 123 UTF-8 bytes.
- `closed`: `True` after the connection has closed.
- `headers` cannot override handshake headers (`Host`, `Upgrade`, `Connection`, `Sec-WebSocket-Key`, `Sec-WebSocket-Version`).

```python
ws = await WebSocket.connect("wss://example.com/chat")
await ws.send("hello")
reply = await ws.recv()
await ws.close(1000, "bye")
```

**Standalone server:**

```python
WebSocketServer(handler, *, ssl=None, max_connections=1000)
```

- `handler` is `async def handler(websocket, path)`. `path` is the request target, including any query string.
- `start(host="127.0.0.1", port=8765)` and `close()`.
- Frames and messages are limited to 16 MiB. The handshake must complete within 10 seconds.
- The `Origin` header is not checked, and the handler receives only the path, not the headers.

```python
async def handler(ws, path):
    while True:
        msg = await ws.recv()
        if msg is None:
            break
        await ws.send(msg)

server = WebSocketServer(handler)
await server.start("127.0.0.1", 8765)
```

`WebSocketError` is raised for invalid handshakes, frames, or use of a closed connection.

### HTTP/2 adapters (extra: `http2`)

```python
from mini_aiohttp import HTTP2Client, HTTP2Server
```

- `HTTP2Client(*, ssl_context=None, timeout=15.0)`: `get(url, *, headers=None)` and `request(method, url, *, headers=None, data=b"")`. Both return `(status, headers, body)`. Only `https://` URLs are supported, and ALPN must negotiate `h2`. Requests to the same origin share one pooled connection and run as parallel streams. The client waits for the server's `MAX_CONCURRENT_STREAMS` before opening a stream. `close()` closes the pooled connections.
- `HTTP2Server(handler, *, ssl_context, max_body_size=10 * 1024 * 1024, max_concurrent_streams=100, max_header_list_size=64 * 1024, idle_timeout=30.0)`: `start(host="127.0.0.1", port=8443)`, the `port` property, and `close(*, grace=5.0)`. The handler is `async def handler(method, path, headers, body)` and returns `(status, headers, body)`. Each stream runs in its own task, so a slow request does not hold up the others. The adapter sets `h2` as the ALPN protocol on `ssl_context`, so use a context dedicated to this server.

Server limits (each violation affects only what it names):

| Situation | Result |
|---|---|
| More than `max_concurrent_streams` open at once | The connection gets GOAWAY (PROTOCOL_ERROR) and is closed. |
| Request headers larger than `max_header_list_size` | That stream is reset (ENHANCE_YOUR_CALM). |
| Body larger than `max_body_size` | That stream is reset. Other streams continue. |
| No bytes received for `idle_timeout` seconds | The connection gets GOAWAY (NO_ERROR) and is closed. |
| Requests that arrive during `close()` | The stream is refused (REFUSED_STREAM). |

`close(grace=...)` stops accepting new connections, lets in-flight streams finish for up to `grace` seconds, then sends GOAWAY(NO_ERROR) and closes the connections.

```python
import ssl

ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
ctx.load_cert_chain("cert.pem", "key.pem")

async def handler(method, path, headers, body):
    return 200, {"content-type": "text/plain"}, b"hello over h2"

server = HTTP2Server(handler, ssl_context=ctx)
await server.start("127.0.0.1", 8443)
```

### HTTP/3 adapters (extra: `http3`)

```python
from mini_aiohttp import HTTP3Client, HTTP3Server
```

HTTP/3 runs over QUIC on UDP and requires TLS 1.3. It is not a fallback for TCP connections, and the endpoint must support HTTP/3.

- `HTTP3Client(*, verify=True, timeout=15.0, max_concurrent_streams=100)`: `get(url, *, headers=None)` and `request(method, url, *, headers=None, data=b"")`. Both return `(status, headers, body)`. `verify=False` disables certificate verification. Requests to the same origin share one pooled QUIC connection. `max_concurrent_streams` caps parallel requests on it, and must not exceed the server's limit. If a connection drops, its pending requests fail with `ConnectionError`, and the next request opens a new connection. `close()` closes the pooled connections.
- `HTTP3Server(handler, *, certificate, private_key, max_body_size=10 * 1024 * 1024, max_concurrent_streams=100, max_header_list_size=64 * 1024, idle_timeout=30.0)`: `start(host="0.0.0.0", port=443)`, the `port` property, and `close(*, grace=5.0)`. `certificate` and `private_key` are file paths. The handler signature is the same as HTTP/2.

Server limits:

| Situation | Result |
|---|---|
| More than `max_concurrent_streams` requests open on one connection | The new stream is reset (H3_EXCESSIVE_LOAD). |
| Request headers larger than `max_header_list_size` | The stream is reset (H3_EXCESSIVE_LOAD). |
| Body larger than `max_body_size` | 413. |
| No traffic for `idle_timeout` seconds | The QUIC connection is closed. |

`close(grace=...)` refuses new requests with H3_REQUEST_REJECTED, which tells the client it is safe to retry elsewhere. In-flight responses finish for up to `grace` seconds, then the QUIC connections close with NO_ERROR.

### Security helpers

```python
from mini_aiohttp import cors, security_headers, RateLimiter, ip_policy, set_cookie, require_json
```

- `cors(*, allow_origins, allow_methods=("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"), allow_headers=("Content-Type", "Authorization"), allow_credentials=False, max_age=600)`: middleware. Only origins in `allow_origins` are reflected, by exact match. `"*"` is allowed only without credentials, otherwise `ValueError`. Preflight requests (OPTIONS with `Access-Control-Request-Method`) get 204.
- `security_headers(*, csp="default-src 'self'", hsts_max_age=None)`: middleware. Adds `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a CSP, and optionally HSTS. Headers the handler already set are kept.
- `RateLimiter(*, rate, burst, max_clients=10_000, clock=time.monotonic)`: token bucket keyed by client IP. `rate` is tokens per second, `burst` is the bucket size. `check(key)` returns `None` if allowed, or the seconds to wait. `middleware()` returns 429 with `Retry-After`. Memory is bounded by `max_clients`.
- `ip_policy(*, allow=None, deny=None)`: middleware that allows or denies IP addresses and CIDR networks. `deny` is checked first. If `allow` is given, only those networks pass. Clients with an unknown address get 403. IPv4-mapped IPv6 clients are checked as IPv4.
- `set_cookie(resp, name, value, *, max_age=None, path="/", secure=True, httponly=True, samesite="Lax")`: adds a `Set-Cookie` header to `resp` and returns `resp`. The value is URL-encoded. `SameSite=None` requires `secure=True`.
- `require_json(schema)`: decorator for handlers. `schema` maps field names to types, or tuples of types such as `(int, float)`. A missing field or wrong type gets 400, and so does invalid JSON. A bool is rejected for an int field.

```python
@app.route("/users", method="POST")
@require_json({"name": str, "age": int})
async def create(req):
    return Response.json({"ok": True})
```

### Compression

```python
from mini_aiohttp import gzip_middleware
```

`gzip_middleware(*, min_size=1024, level=6)` compresses buffered responses for clients that accept gzip. It skips bodies under `min_size`, responses that already have `Content-Encoding`, and types that are not text, JSON, JavaScript, XML or SVG. Streamed responses pass through unchanged. It adds `Vary: Accept-Encoding`. `level` must be 1 to 9.

### Multipart uploads

- Client: `session.request("POST", url, form={"title": "x"}, files={"avatar": ("a.png", data, "image/png")})`.
- Server: `req.form()` returns `{name: [str or UploadedFile]}`. `UploadedFile` is a dataclass with `filename`, `content_type` and `data`. At most 200 parts are accepted.

### Proxies

- `proxy="http://host:port"`: HTTP proxy. HTTPS targets use CONNECT tunnels.
- `proxy="socks5://user:pass@host:port"`: SOCKS5 proxy. DNS is resolved locally.
- `proxy="socks5h://..."`: SOCKS5 proxy with DNS resolved by the proxy.

An HTTP proxy must support CONNECT for HTTPS. Proxy authentication is supported for SOCKS5 only.

### Optional accelerators

- `use_uvloop()`: switches asyncio to uvloop if it is installed. Call it before `asyncio.run()`. Returns `True` when it switched.
- `brotli` extra: enables `br` decoding. Without it, a `br` response raises `ClientError`.
- `orjson` extra: used automatically by `Response.json`, `Request.json` and the JSON helpers when installed.
- `certifi` extra: the default TLS context uses the certifi CA bundle.

### Extras (`mini_aiohttp.extras`)

Each extra imports its package only when it is used.

- `metrics.prometheus_middleware(registry=None, *, namespace="http")` and `metrics_handler(registry=None)`: counts requests by method and status, records latency by method, and serves the Prometheus text format. Extra: `prometheus`.
- `jwt_auth.jwt_auth(secret, *, algorithms=("HS256",), audience=None, issuer=None, exempt=())`: requires `Authorization: Bearer <token>`. Decoded claims are stored in `req.user`. Missing or invalid tokens get 401 with `WWW-Authenticate`. `exempt` lists exact paths that skip authentication. Extra: `jwt`.
- `pydantic_json.validated_json(model)`: decorator that parses the body into a pydantic v2 model and stores it in `req.model`. Invalid JSON gets 400. Validation errors get 422 with `{"detail": [...]}`. Extra: `pydantic`.
- `redis_limiter.RedisRateLimiter(client, *, rate, window=1, prefix="mini-aiohttp:rl", clock=time.time)`: fixed-window rate limit shared across processes. `client` is any async Redis client with `incr` and `expire`, such as `redis.asyncio.Redis`. `check(key)` is async. `middleware()` returns 429 with `Retry-After`. No extra package is needed.
- `tracing.tracing_middleware(*, tracer_name="mini_aiohttp")`: creates one server span per request, with the method, path and status code. Spans use the globally configured OpenTelemetry tracer. Extra: `otel`.

### Errors

- `ClientError`: client-side failures, including malformed responses, oversized bodies, too many redirects and unsupported encodings.
- `WebSocketError`: WebSocket handshake, frame and connection errors.
- `FormError`: a form body that cannot be parsed. It has a `status` for the response.
- `TemplateError`: a missing template, or a variable missing from the template context.
- `TelegramError`: Telegram answered with `ok=false`, or with an unexpected reply. Its `description` attribute holds Telegram's message.
- `ValueError`: invalid arguments, such as an unsupported URL scheme, an invalid header, or a session secret shorter than 32 bytes.
- The server never lets a handler exception reach the client. It returns 500 and logs the traceback.

---

## 🌊 Streaming Responses

Create asynchronous chunked responses without buffering the complete response body.

```python
import asyncio
from mini_aiohttp import Server, Response

app = Server()

@app.route("/stream")
async def stream(req):

    async def generate():
        for i in range(5):
            yield f"Message {i}\n"
            await asyncio.sleep(1)

    return Response.stream(
        generate(),
        headers={
            "Content-Type": "text/plain"
        }
    )

asyncio.run(
    app.serve_forever("127.0.0.1", 8080)
)
```

Supports HTTP/1.1 chunked response transfer.

---

## 📊 Server Statistics

Monitor basic server activity:

```python
print(app.stats)
```

Example output:

```python
{
    "active_connections": 0,
    "total_connections": 1,
    "total_requests": 2,
    "status_counts": {
        200: 1,
        404: 1
    },
    "uptime_requests_per_connection": 2.0
}
```

Statistics are process-local and represent basic runtime counters.

---

## 🧪 Testing

From a source checkout, install the development tools:

```bash
pip install -e ".[dev,all]"
```

Run tests with coverage:

```bash
python -m pytest -v --cov
```

Lint, format and type-check:

```bash
ruff check .
ruff format --check .
mypy
```

Enable the git hooks once:

```bash
pre-commit install
```

Build distributions:

```bash
pip install build twine
python -m build
python -m twine check dist/*
```

---

## ⚙️ Compatibility

| Component | Support |
|---|---|
| Python | 3.11+ |
| Type hints | Yes (`py.typed`) |
| HTTP/1.1 | Yes |
| HTTPS Server | Yes |
| Chunked Requests | Yes |
| Streaming Responses | Yes |
| Content-Encoding | gzip, deflate (core); br (`brotli` extra) |
| Response compression | gzip middleware (core) |
| Static files | `StaticFiles`: Range (single range), ETag, Last-Modified, conditional GET |
| Sessions | `SessionMiddleware`: signed cookies, HMAC-SHA256 |
| Forms and templates | `parse_urlencoded`, `Template`, `TemplateLoader` |
| Retries | `RetryPolicy`: 429 and 502/503/504 with Retry-After and jittered backoff |
| SOCKS5 and Tor | Yes, HTTP targets; `tor_proxy()` preset |
| Webhooks and Telegram | `WebhookRouter`, `TelegramPoller` |
| HTTP/2 | Optional adapters (`HTTP2Client`, `HTTP2Server`): pooled client, parallel streams, limits, graceful close |
| HTTP/3 | Optional adapters (`HTTP3Client`, `HTTP3Server`): pooled client, parallel streams, limits, graceful close |
| WebSockets | RFC 6455 client; server on the HTTP port (`Server.websocket`) or standalone (`WebSocketServer`) |
| ASGI 3 | `ASGIApp` for HTTP and lifespan (no WebSocket) |
| Prebuilt wheels | Linux x86_64, macOS x86_64 and arm64, Windows x86_64 (CPython 3.11–3.13) |

---

## 🛡️ Protocol Limitations

- HTTP/2 adapters require TLS ALPN negotiation. The client keeps one connection per origin. The server enforces its own stream, header and idle limits, which are listed in the HTTP/2 section.
- HTTP/3 requires QUIC/UDP and TLS 1.3 certificates, plus an HTTP/3-capable endpoint. It cannot be transparently substituted on arbitrary TCP connections.
- HTTP/3 `max_concurrent_streams` is enforced by the application. aioquic advertises a fixed QUIC stream limit of 128 per connection, so keep the server's limit at or below that.
- WebSocket endpoints on the HTTP port (`Server.websocket`) bypass middleware, so authentication and Origin checks belong in the handler. The standalone `WebSocketServer` has the same limits.
- WebSocket Origin checking is not built in. If you authenticate with cookies, validate the `Origin` header in your handler.
- WebSocket compression (`permessage-deflate`) is not implemented.
- `StaticFiles` serves one byte range per request. Multi-range requests are answered with the full file. Range and ETag are not available through the older `Server.static()` handler.
- `StaticFiles` ETags are based on size and modification time, not content hashes.
- Sessions are signed, not encrypted. The data is readable by the client, and a stolen cookie is valid until it expires.
- The template engine has no loops, conditions or template inheritance. Use Python for logic.
- No automatic multi-process worker manager is included. Use your ASGI server's worker options (for example `uvicorn --workers`) with `ASGIApp`.
- WebSockets are not available through `ASGIApp`. Use `Server.websocket` on the built-in server, or run `WebSocketServer` on its own port.
- `TelegramPoller` and `WebhookRouter` do not handle Telegram's file downloads or payment flows. They only deliver updates to your handler.

Additional notes:

- HTTP/2 and HTTP/3 are optional adapters and require their respective extras.
- `AutoClientSession` uses HTTP/2 only when no custom `ssl_context` is given.
- `max_response_size` limits encoded and decoded response sizes. Gzip/deflate use zlib's bounded-output API; Brotli's Python decoder has no output-length parameter, so its output is checked after each decoder call rather than hard-capped during allocation.
- HTTPS proxy tunnels require a CONNECT-capable HTTP proxy or SOCKS5 proxy.
- Streaming responses require asynchronous iterables.
- Under `ASGIApp`, the ASGI server enforces the request-line, header-count and keep-alive limits from the first table above. `max_connections` and `read_timeout` on `Server` apply only to the built-in `start()` and `serve_forever()` path.
- Prebuilt wheels are not published for Linux aarch64, 32-bit platforms or musl Linux. Those platforms install from the source distribution.

---

## 📚 Links

- **PyPI Package:** https://pypi.org/project/mini-aiohttp/

---

## 📄 License

This project is licensed under the MIT License.

---

**mini-aiohttp v1.0.0**

*Lightweight. Asynchronous. Flexible.*
