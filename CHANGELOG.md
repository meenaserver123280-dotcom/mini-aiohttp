# Changelog

## 0.7.0

### Added
- `StaticFiles` middleware (`mini_aiohttp.static`): single byte ranges with 206 and 416 responses, `Accept-Ranges`, `ETag` and `Last-Modified`, `If-None-Match`, `If-Modified-Since`, `If-Range`, and 405 for methods other than GET and HEAD. Paths cannot leave the served directory.
- `SessionMiddleware` and `Session` (`mini_aiohttp.sessions`): signed-cookie sessions (HMAC-SHA256) with expiry. Tampered, forged, expired or wrongly signed cookies are ignored. Cookies default to `Secure`, `HttpOnly` and `SameSite=Lax`. Clearing a session expires the cookie. Session data must fit in a cookie of about 3.8 KiB.
- `parse_urlencoded` and `FormError` (`mini_aiohttp.forms`): parse `application/x-www-form-urlencoded` bodies with size and field-count limits. Errors carry the status to return: 415, 413 or 400.
- `Template`, `TemplateLoader`, `TemplateError` and `html_response` (`mini_aiohttp.templates`): a minimal template engine with HTML escaping by default, a raw `{{! }}` form, dotted lookups, a missing-variable error, and a cached loader with path-traversal protection.
- Reason phrases for 206 and 416 in `Response`.
- All of the above are exported from the top-level package.

### Changed
- Version bumped to 0.7.0. The README documents the new modules, and the limitation that static files had no `Range` or `ETag` support is removed.

### Notes
- HTTP/2 and HTTP/3 production hardening, WebSocket on the same HTTP port, and load tests against real traffic are not part of this release.

## 0.6.1

### Added
- Prebuilt wheels for Linux x86_64 (manylinux), macOS (x86_64 and arm64) and Windows x86_64, for Python 3.11, 3.12 and 3.13. Wheels include the optional native Rust extension (`mini_aiohttp._native`). Other platforms install from the source distribution and use the pure-Python fallback when no Rust toolchain is available.

### Packaging
- The release workflow builds wheels with cibuildwheel and smoke-tests the native extension in each wheel before anything is uploaded.
- A wheel build also runs on pushes to main that touch the package, so packaging problems show up before a release.
- Publishing uploads the source distribution and all wheels together.

### Notes
- Version 0.6.0 was published as a source distribution only. Version 0.6.1 has the same library code as 0.6.0, plus the packaging changes above.

## 0.6.0

### Added
- `ASGIApp`: an ASGI 3 adapter for running a `Server` under uvicorn, hypercorn and other ASGI servers. Routing, middleware, static files and the 404, 405 and 500 responses come from `Server`. Covers HTTP (buffered, streamed and HEAD), request body limits, client disconnects and lifespan hooks. WebSocket scopes are refused; use `WebSocketServer` on its own port.

### Changed
- Version bumped to 0.6.0. The README documents the ASGI adapter.

### Verification
- CI run 44451874737 passed on Python 3.11, 3.12 and 3.13, including the full test suite and the distribution build.

### Packaging
- Source distribution only.

## 0.5.9

### Changed
- README: full API reference, protocol limitations and optional extras. Removed links to the private repository.
- `pyproject.toml` and `mini_aiohttp.__version__` both report 0.5.9. `__version__` previously reported 0.5.6.

### Notes
- Version 0.5.8 was never released.

## 0.5.7

### Fixed
- Correct native response serialization to emit real HTTP CRLF delimiters and preserve Python serializer parity.
- Fix response handling regressions exposed by the native serializer tests.
- Keep the optional Rust extension source and build manifest in the repository.

### Packaging
- Publish the source distribution only. The previous release attempted to upload a Linux wheel tagged `linux_x86_64`, which PyPI rejects as an unsupported platform tag.
- Update project links to the current GitHub repository.

### Verification
- CI run 44353700235 passed on Python 3.11, 3.12 and 3.13, including native-extension verification and the full test suite.

## 0.5.6

### Added
- PyPI publishing workflow, initially configured for trusted publishing and later switched to repository API-token publishing.

### Notes
- No package code changes were intended in this release.

## 0.5.5

### Added
- Optional Rust/PyO3 request-head parsing and buffered response serialization, with pure-Python fallbacks.
- Native parsing without creating intermediate Python line lists when the extension is available.
- Native extension build verification in CI, and repeated median server benchmarks.

### Fixed
- Connections refused with 503 now drain the client's unread request before closing, which avoids connection resets.
- Removed stray files that were committed by mistake, and added a `.gitignore` for Python and Rust build output.
- Rebuilt this changelog, which had stray tool-output lines committed.

### Verification
- Test suite passes on Python 3.11, 3.12 and 3.13 in CI (run 44351793227).
- Not yet verified for this release: native and Python fallback parity tests, stress and malformed-request tests, Windows and macOS wheel builds, and benchmark results against aiohttp.

### Known issues
- The WebSocket connect-timeout test can leave a pending task behind, which shows up as a "Task was destroyed" warning. Cleanup is planned for the next release.

## 0.5.4

### Fixed
- Enforce decompressor-level output limits for gzip and deflate responses to reduce decompression-bomb memory exposure.
- Add regression coverage for bounded and fragmented decompression, exact limits, and raw-deflate fallback.

### Notes
- Brotli output is validated after each decoder call; the Python Brotli API does not expose a hard output-length limit.

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
