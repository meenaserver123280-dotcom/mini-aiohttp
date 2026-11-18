# Changelog

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
- A new CI run is being triggered for this version bump before tagging the release.

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
- Optional `orjson` fast path for `Response.json` and `Request.json` (`orjson` extra).
- Optional `certifi` CA bundle for HTTPS.
