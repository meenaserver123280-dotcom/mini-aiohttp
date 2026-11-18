# Changelog

## 0.5.3

### Fixed
- Streamed responses now enforce the cumulative decoded size limit (`max_response_size`), including decompressed bodies.
- Gzip/deflate decompression now uses zlib's `max_length` to stop output at the configured limit plus one detection byte.
- README version references are synchronized with package metadata.

### Changed
- Minimum supported Python version is now 3.11. Python 3.9 and 3.10 are no longer supported, because HTTPS proxy tunnels rely on `StreamWriter.start_tls()`, which requires 3.11+.

## 0.5.0

### Added
- Server-side gzip compression as middleware (`gzip_middleware`), stdlib only, honours Accept-Encoding q-values and skips compressed, small or binary responses.
- Optional integrations under `mini_aiohttp.extras` (each one imports its package only when used):
  - `metrics`: Prometheus middleware and `/metrics` handler (extra `prometheus`)
  - `jwt_auth`: JWT bearer authentication middleware, sets `req.user` (extra `jwt`)
  - `pydantic_json`: `validated_json` decorator, sets `req.model`, replies 422 on invalid input (extra `pydantic`)
  - `tracing`: OpenTelemetry server spans per request (extra `otel`)
  - `redis_limiter`: rate limiter shared across processes; bring your own async Redis client, no extra needed
- `py.typed` marker (PEP 561), so type checkers use the inline hints.
- Hypothesis fuzzing of the request-target parser, route matcher, multipart parser and cookie parser.
- Dev tooling: ruff and mypy configuration, coverage configuration, pre-commit hooks, and a CI lint job (non-blocking until the existing code is clean).
- `dev` extra: `pip install -e ".[dev]"`.

### Install
- `pip install "mini-aiohttp[prometheus]"`, `[jwt]`, `[pydantic]`, `[otel]`
- `pip install "mini-aiohttp[all]"` installs every runtime extra.

## 0.4.0

### Added
- Automatic response decoding for `Content-Encoding: gzip`, `x-gzip` and `deflate` (standard library, no extra dependency), for buffered and streamed responses.
- `Accept-Encoding` is sent by default (`gzip, deflate`, plus `br` when brotli is installed). Callers can override it.
- Optional `br` (Brotli) decoding via the `brotli` extra.
- Optional `orjson` fast path for `Response.json` and `Request.json` (`orjson` extra). Falls back to stdlib `json`.
- Optional `certifi` CA bundle for the HTTP/1.1 client's default TLS context (`certifi` extra).
- `mini_aiohttp.use_uvloop()` to opt in to uvloop as the event loop (`speed` extra, Linux and macOS).
- `pytest-timeout` in the `test` extra; tests now fail after 30 seconds instead of hanging.

### Changed
- `ClientSession` sends `Accept-Encoding` and transparently decodes compressed bodies. `response.headers` no longer contains `content-encoding` or `content-length` after decoding, because they describe the encoded body.
- Brotli responses without the `brotli` package raise `ClientError` with a hint to install the extra.

## 0.3.1

### Fixed
- WebSocket: a failed or timed-out `connect()` now closes its socket instead of leaking it.
- WebSocket: network errors surface as `WebSocketError`, not raw `ConnectionResetError`.
- WebSocket server: requests without a `Host` header are rejected; oversized or excess headers are rejected cleanly instead of escaping as unhandled exceptions.
- WebSocket test suite no longer hangs when a handler fails; added a connect-timeout test.
- HTTP/2 server: malformed clients drop only their own connection (`H2Error`) instead of crashing the handler.
- Cookies: `Max-Age` is honoured; expired cookies are no longer sent.
- Client: a header is never sent twice (for example `user-agent` plus `User-Agent`); the client rejects caller-supplied `Content-Length` and `Transfer-Encoding`.
- Client: closing a streamed response releases its connection even if the body was never read.
- `AutoClientSession` no longer breaks HTTP/1.1 when a custom `ssl_context` is given (HTTP/2 is skipped in that case).
- CI: HTTP/3 extra is installed on a best-effort basis so the Python 3.9 matrix job no longer fails on `aioquic`.
- README: `response.text()` example and streaming example newline fixed.

### Added
- `Response.text()` for buffered responses.
- Regression tests for header de-duplication, framing-header guard and cookie expiry.

## 0.3.0

### Added
- Standalone WebSocket client and server (RFC 6455).
- Optional HTTP/2 and HTTP/3 client and server adapters (`mini-aiohttp[http2]`, `mini-aiohttp[http3]`).
- `AutoClientSession` with ALPN-based HTTP/2 selection and Alt-Svc HTTP/3 discovery.
- Cookie jar with Domain, Path, Secure and Max-Age handling.

## 0.2.0 - 2026-11-16

### Added
- HTTP/1.1 chunked request-body decoding with bounded size checks and trailer validation.
- Chunked server response streaming through Response.stream().
- Lightweight process-local Server.stats counters.
- SOCKS5/SOCKS5h proxy support for HTTP targets.
- Multipart request encoding and parsing, static file serving, HTTPS server support and security middleware.

### Improved
- Request framing validation rejects Content-Length plus Transfer-Encoding.
- Streaming uploads are not replayed on redirects or retries.
- Client retries are limited to idempotent methods and bounded backoff.
- Documentation now reflects implemented features and explicit protocol limitations.
- CI now tests Python 3.9 through 3.13 and validates built distributions.

### Not included
- HTTP/2, HTTP/3, WebSockets and HTTPS-over-proxy support.