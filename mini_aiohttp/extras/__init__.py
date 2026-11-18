"""Optional integrations. Each module imports its third-party package only when used.

- metrics: Prometheus middleware and /metrics handler (extra: prometheus)
- jwt_auth: JWT bearer authentication middleware (extra: jwt)
- pydantic_json: pydantic-validated JSON bodies (extra: pydantic)
- tracing: OpenTelemetry server spans (extra: otel)
- redis_limiter: Redis-backed rate limiter (bring your own redis client, no extra)
"""
