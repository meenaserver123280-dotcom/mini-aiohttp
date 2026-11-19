"""Traffic recorder: an optional server middleware that keeps the most recent requests.

    recorder = TrafficRecorderMiddleware(maxlen=500, body_preview=200)
    app.middleware_(recorder)
    ...
    for entry in recorder.records:
        print(entry["method"], entry["path"], entry["status"], entry["duration_ms"])

Each record is a dict with: method, path, status, duration_ms, client, body_preview.
`body_preview` is off by default (0 bytes). Request bodies can contain passwords and
tokens, so turn it on only for debugging.
"""
from __future__ import annotations

import time
from collections import deque


class TrafficRecorderMiddleware:
    def __init__(self, *, maxlen: int = 500, body_preview: int = 0, clock=time.perf_counter):
        if maxlen < 1:
            raise ValueError("maxlen must be at least 1")
        if body_preview < 0:
            raise ValueError("body_preview must be 0 or more")
        self._records: deque = deque(maxlen=maxlen)
        self._preview_bytes = body_preview
        self._clock = clock

    async def __call__(self, req, call_next):
        started = self._clock()
        status = 500  # stays 500 if the handler raises
        try:
            response = await call_next(req)
            status = response.status
            return response
        finally:
            self._records.append({
                "method": req.method,
                "path": req.path,
                "status": status,
                "duration_ms": round((self._clock() - started) * 1000, 3),
                "client": req.client,
                "body_preview": self._preview(req.body),
            })

    def _preview(self, body: bytes) -> str:
        if not body or self._preview_bytes == 0:
            return ""
        return body[: self._preview_bytes].decode("utf-8", errors="replace")

    @property
    def records(self) -> list:
        """Recorded requests, oldest first. A copy, so changing it does not affect the recorder."""
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()
