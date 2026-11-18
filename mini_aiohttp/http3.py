"""Optional HTTP/3 client and server adapters based on aioquic.

Install with: pip install mini-aiohttp[http3]
HTTP/3 uses QUIC over UDP and TLS 1.3; it is not a fallback transport for
ordinary TCP sockets. Callers must provide/resolve an HTTP/3-capable endpoint.
"""
from __future__ import annotations

import asyncio
import ssl
from urllib.parse import urlsplit


def _imports():
    try:
        from aioquic.asyncio import connect, serve
        from aioquic.asyncio.protocol import QuicConnectionProtocol
        from aioquic.h3.connection import H3_ALPN, H3Connection
        from aioquic.h3.events import DataReceived, HeadersReceived
        from aioquic.quic.configuration import QuicConfiguration
    except ImportError as exc:
        raise RuntimeError("HTTP/3 support requires 'pip install mini-aiohttp[http3]'") from exc
    return {
        "connect": connect,
        "serve": serve,
        "QuicConnectionProtocol": QuicConnectionProtocol,
        "H3_ALPN": H3_ALPN,
        "H3Connection": H3Connection,
        "DataReceived": DataReceived,
        "HeadersReceived": HeadersReceived,
        "QuicConfiguration": QuicConfiguration,
    }


class HTTP3Client:
    """Minimal HTTP/3 client. Uses QUIC/UDP and TLS 1.3 via aioquic."""

    def __init__(self, *, verify=True, timeout=15.0):
        self.verify = verify
        self.timeout = timeout

    async def get(self, url, *, headers=None):
        return await self.request("GET", url, headers=headers)

    async def request(self, method, url, *, headers=None, data=b""):
        lib = _imports()
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("HTTP/3 requires an https:// URL")
        host, port = parts.hostname, parts.port or 443
        config = lib["QuicConfiguration"](is_client=True, alpn_protocols=lib["H3_ALPN"])
        if not self.verify:
            config.verify_mode = ssl.CERT_NONE
        H3Connection = lib["H3Connection"]
        DataReceived = lib["DataReceived"]
        HeadersReceived = lib["HeadersReceived"]

        def finish(state):
            future = state["future"]
            if not future.done():
                future.set_result((
                    int(state["headers"].get(":status", "0")),
                    dict(state["headers"]),
                    bytes(state["body"]),
                ))

        class Protocol(lib["QuicConnectionProtocol"]):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.http = H3Connection(self._quic)
                self.pending = {}

            async def request_stream(self, request_headers, body):
                stream_id = self._quic.get_next_available_stream_id()
                future = asyncio.get_running_loop().create_future()
                self.pending[stream_id] = {"future": future, "headers": {}, "body": bytearray()}
                self.http.send_headers(stream_id, request_headers, end_stream=not body)
                if body:
                    self.http.send_data(stream_id, body, end_stream=True)
                self.transmit()
                return await future

            def quic_event_received(self, event):
                for http_event in self.http.handle_event(event):
                    stream_id = getattr(http_event, "stream_id", None)
                    state = self.pending.get(stream_id)
                    if state is None:
                        continue
                    if isinstance(http_event, HeadersReceived):
                        state["headers"].update({k.decode(): v.decode() for k, v in http_event.headers})
                    elif isinstance(http_event, DataReceived):
                        state["body"].extend(http_event.data)
                    if getattr(http_event, "stream_ended", False):
                        finish(state)
                        self.pending.pop(stream_id, None)

        async def run():
            async with lib["connect"](host, port, configuration=config, create_protocol=Protocol) as protocol:
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                request_headers = [
                    (b":method", method.upper().encode()),
                    (b":scheme", b"https"),
                    (b":authority", parts.netloc.encode()),
                    (b":path", path.encode()),
                ]
                for key, value in (headers or {}).items():
                    name = key.lower()
                    if name.startswith(":") or name in ("connection", "transfer-encoding", "upgrade"):
                        raise ValueError("invalid HTTP/3 header")
                    request_headers.append((name.encode(), str(value).encode()))
                payload = data.encode() if isinstance(data, str) else bytes(data)
                return await protocol.request_stream(request_headers, payload)

        return await asyncio.wait_for(run(), self.timeout)


class HTTP3Server:
    """HTTP/3 server. Handler: async handler(method, path, headers, body) -> (status, headers, body)."""

    def __init__(self, handler, *, certificate, private_key, max_body_size=10 * 1024 * 1024):
        self.handler = handler
        self.certificate = certificate
        self.private_key = private_key
        self.max_body_size = max_body_size
        self._server = None
        self._tasks: set = set()  # keep references so responses are not garbage-collected

    async def start(self, host="0.0.0.0", port=443):
        lib = _imports()
        config = lib["QuicConfiguration"](is_client=False, alpn_protocols=lib["H3_ALPN"])
        config.load_cert_chain(self.certificate, self.private_key)
        handler = self.handler
        max_body_size = self.max_body_size
        H3Connection = lib["H3Connection"]
        DataReceived = lib["DataReceived"]
        HeadersReceived = lib["HeadersReceived"]
        tasks = self._tasks

        class Protocol(lib["QuicConnectionProtocol"]):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.http = H3Connection(self._quic)
                self.requests = {}

            def _spawn(self, stream_id):
                task = asyncio.ensure_future(self.respond(stream_id))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

            def quic_event_received(self, event):
                for http_event in self.http.handle_event(event):
                    stream_id = getattr(http_event, "stream_id", None)
                    if stream_id is None:
                        continue
                    if isinstance(http_event, HeadersReceived):
                        self.requests[stream_id] = {
                            "headers": {k.decode(): v.decode() for k, v in http_event.headers},
                            "body": bytearray(),
                        }
                        if http_event.stream_ended:
                            self._spawn(stream_id)
                    elif isinstance(http_event, DataReceived):
                        state = self.requests.get(stream_id)
                        if state is None:
                            continue
                        state["body"].extend(http_event.data)
                        if len(state["body"]) > max_body_size:
                            self.requests.pop(stream_id, None)
                            self.http.send_headers(stream_id, [(b":status", b"413")], end_stream=True)
                            self.transmit()
                        elif http_event.stream_ended:
                            self._spawn(stream_id)

            async def respond(self, stream_id):
                state = self.requests.pop(stream_id, None)
                if state is None:
                    return
                headers = state["headers"]
                try:
                    status, response_headers, body = await handler(
                        headers.get(":method", "GET"), headers.get(":path", "/"),
                        {k: v for k, v in headers.items() if not k.startswith(":")},
                        bytes(state["body"]),
                    )
                    payload = body.encode() if isinstance(body, str) else bytes(body)
                    out = [(b":status", str(int(status)).encode())]
                    out.extend((str(k).lower().encode(), str(v).encode()) for k, v in response_headers.items())
                    out.append((b"content-length", str(len(payload)).encode()))
                    self.http.send_headers(stream_id, out, end_stream=not payload)
                    if payload:
                        self.http.send_data(stream_id, payload, end_stream=True)
                except Exception:
                    self.http.send_headers(
                        stream_id, [(b":status", b"500"), (b"content-length", b"0")], end_stream=True
                    )
                self.transmit()

        self._server = await lib["serve"](host, port, configuration=config, create_protocol=Protocol)
        return self._server

    async def close(self):
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


__all__ = ["HTTP3Client", "HTTP3Server"]
