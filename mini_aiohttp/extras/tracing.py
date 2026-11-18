"""OpenTelemetry tracing middleware (optional).

Requires: pip install "mini-aiohttp[otel]"

    from mini_aiohttp.extras.tracing import tracing_middleware

    app.middleware_(tracing_middleware(service_name="my-api"))

Creates one server span per request with HTTP method, target and status code.
The tracer comes from the globally configured OpenTelemetry SDK/exporter.
"""
from __future__ import annotations


def _otel():
    try:
        from opentelemetry import trace
    except ImportError as exc:
        raise RuntimeError('tracing needs: pip install "mini-aiohttp[otel]"') from exc
    return trace


def tracing_middleware(*, tracer_name: str = "mini_aiohttp"):
    trace = _otel()
    tracer = trace.get_tracer(tracer_name)

    async def middleware(req, call_next):
        with tracer.start_as_current_span(f"{req.method} {req.path}", kind=trace.SpanKind.SERVER) as span:
            span.set_attribute("http.request.method", req.method)
            span.set_attribute("url.path", req.path)
            resp = await call_next(req)
            span.set_attribute("http.response.status_code", resp.status)
            if resp.status >= 500:
                span.set_status(trace.Status(trace.StatusCode.ERROR))
            return resp

    return middleware
