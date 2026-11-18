# ⚡ mini-aiohttp

**mini-aiohttp v0.5.3** is a lightweight, asynchronous HTTP/1.1 client and server framework built with Python and asyncio.

Designed for simplicity, performance, flexibility and security. The core has zero runtime dependencies; faster or extra features are opt-in extras.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/Version-0.5.3-green)](https://github.com/hostunio16-ops/mini-aiohttp/releases/tag/v0.5.3)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## ✨ Features

### HTTP Client
- Asynchronous HTTP/1.1 requests.
- Connection pooling and keep-alive.
- Per-host concurrency limits.
- Automatic redirect handling.
- Bounded retries for idempotent methods.
- Cookie management, including Max-Age expiry.
- Automatic decoding of `gzip`, `deflate` and (with an extra) `br` responses.
- Streaming response downloads.
- Async streaming uploads.
- Multipart form and file uploads.
- HTTP and SOCKS5/SOCKS5h proxies, including HTTPS CONNECT/tunnelling.
- DNS caching.
- Configurable connection and request timeouts.
- TLS context customization, and optional certifi CA bundle.
- Request and response hooks.
- Response size limits.

- Standalone WebSocket client and server (`ws://` / `wss://`).
- Automatic HTTPS client: ALPN selects HTTP/2 when available, otherwise HTTP/1.1; Alt-Svc enables HTTP/3 on subsequent requests (`AutoClientSession`).
- Optional HTTP/2 client/server adapters using ALPN (`pip install mini-aiohttp[http2]`).
- Optional HTTP/3 client/server adapters using QUIC (`pip install mini-aiohttp[http3]`).

### HTTP Server
- Asynchronous HTTP server.
- Custom routing and path parameters.
- Middleware support.
- Gzip response compression middleware (`gzip_middleware`).
- HTTP/1.1 persistent connections.
- Chunked request-body decoding.
- Chunked streaming responses.
- HTTPS support using SSLContext.
- Static file serving.
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
- JWT bearer authentication middleware (extra).
- JSON request validation, plain or with pydantic models (extra).
- Bounded request and response parsing, fuzz-tested with hypothesis.

### Observability (extras)
- Prometheus metrics middleware and `/metrics` handler.
- OpenTelemetry server spans.

---

## 📦 Installation

Core (no dependencies):

```bash
pip install mini-aiohttp
```

Requires Python 3.11 or newer.

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
pip install mini-aiohttp==0.5.3
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
        "version": "0.5.3"
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

Clone the repository:

```bash
git clone https://github.com/hostunio16-ops/mini-aiohttp.git
cd mini-aiohttp
```

Install development tools:

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
| SOCKS5 | Yes, HTTP targets |
| HTTP/2 | Optional adapters (`HTTP2Client`, `HTTP2Server`) |
| HTTP/3 | Optional adapters (`HTTP3Client`, `HTTP3Server`) |
| WebSockets | Standalone RFC 6455 client/server |

---

## 🛡️ Protocol Limitations

- HTTP/2 and HTTP/3 are optional adapters and require their respective extras.
- HTTP/2 requires TLS ALPN negotiation; the adapter currently uses one request per connection.
- `AutoClientSession` uses HTTP/2 only when no custom `ssl_context` is given.
- HTTP/3 requires QUIC/UDP, TLS 1.3 certificates and an HTTP/3-capable endpoint; it cannot be transparently substituted on arbitrary TCP connections.
- WebSocket support is standalone and is not mounted as a route on the HTTP/1.1 `Server` class.
- WebSocket servers do not check the `Origin` header; if you authenticate with cookies, validate `Origin` in your handler.
- WebSocket `permessage-deflate` (compression) is not implemented yet.
- The server does not answer `HEAD` requests yet, and static files do not support `Range` requests or `ETag` yet.
- Compressed streamed responses are decoded as they arrive; `max_response_size` limits the encoded size, not the decoded size.
- HTTPS proxy tunnels require a CONNECT-capable HTTP proxy or SOCKS5 proxy.
- Streaming responses require asynchronous iterables.
- No automatic multi-process worker manager is included.
- ASGI compatibility (running under uvicorn or gunicorn) is not provided.

---

## 📚 Links

- **GitHub Repository:** https://github.com/hostunio16-ops/mini-aiohttp
- **Latest Release:** https://github.com/hostunio16-ops/mini-aiohttp/releases/tag/v0.5.3
- **PyPI Package:** https://pypi.org/project/mini-aiohttp/
- **GitHub Actions:** https://github.com/hostunio16-ops/mini-aiohttp/actions

---

## 📄 License

This project is licensed under the MIT License.

---

**mini-aiohttp v0.5.3**

*Lightweight. Asynchronous. Flexible.*
