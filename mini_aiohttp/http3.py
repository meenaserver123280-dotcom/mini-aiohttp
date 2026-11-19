"""Optional HTTP/3 client and server adapters based on aioquic.

Install with: pip install mini-aiohttp[http3]
HTTP/3 uses QUIC over UDP and TLS 1.3; it is not a fallback transport for
ordinary TCP sockets. Callers must provide/resolve an HTTP/3-capable endpoint.
"""
from __future__ import annotations

import asyncio
import socket
import ssl
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

# HTTP/3 error codes (RFC 9114 section 8.1) used by the server.
H3_NO_ERROR = 0x100
H3_EXCESSIVE_LOAD = 0x107
H3_REQUEST_REJECTED = 0x10B


def _imports():
    try:
        from aioquic.asyncio import connect, serve
        from aioquic.asyncio.protocol import QuicConnectionProtocol
        from aioquic.h3.connection import H3_ALPN, H3Connection
        from aioquic.h3.events import DataReceived, HeadersReceived
        from aioquic.quic.configuration import QuicConfiguration
        from aioquic.quic.events import ConnectionTerminated, StreamReset
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
        "ConnectionTerminated": ConnectionTerminated,
        "StreamReset": StreamReset,
    }


@asynccontextmanager
async def _connect(lib, host, port, configuration, create_protocol):
    """Like aioquic's connect(), but the UDP socket matches the address family.

    aioquic always opens a dual-stack IPv6 socket, which fails on hosts without IPv6.
    """
    from aioquic.quic.connection import QuicConnection

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    family, _, _, _, addr = infos[0]
    if configuration.server_name is None:
        configuration.server_name = host
    connection = QuicConnection(configuration=configuration)
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.bind(("::", 0, 0, 0))
        else:
            sock.bind(("0.0.0.0", 0))
    except BaseException:
        sock.close()
        raise
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: create_protocol(connection), sock=sock
    )
    try:
        protocol.connect(addr)
        await protocol.wait_connected()
        yield protocol
    finally:
        protocol.close()
        await protocol.wait_closed()
        transport.close()


def _header_list_size(headers):
    """RFC 9114 section 4.2.2: name + value + 32 bytes per field."""
    return sum(len(k) + len(v) + 32 for k, v in headers)


class HTTP3Client:
    """HTTP/3 client over QUIC. One pooled QUIC connection per origin.

    Requests to the same origin share a connection, up to `max_concurrent_streams`
    at a time; extra requests wait for a free slot. The value must not exceed the
    server's stream limit. If a connection drops, its pending requests fail at once
    and the next request opens a new connection. Call `await client.close()` to
    close the pooled connections.
    """

    def __init__(self, *, verify=True, timeout=15.0, max_concurrent_streams=100):
        if max_concurrent_streams < 1:
            raise ValueError("max_concurrent_streams must be at least 1")
        self.verify = verify
        self.timeout = timeout
        self.max_concurrent_streams = max_concurrent_streams
        self._pool: dict = {}  # (host, port) -> (protocol, exit_stack)
        self._connect_locks: dict = {}

    async def get(self, url, *, headers=None):
        return await self.request("GET", url, headers=headers)

    async def _protocol_for(self, lib, host, port):
        key = (host, port)
        entry = self._pool.get(key)
        if entry is not None and not entry[0].dead:
            return entry[0]
        lock = self._connect_locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._pool.get(key)
            if entry is not None and not entry[0].dead:
                return entry[0]
            config = lib["QuicConfiguration"](is_client=True, alpn_protocols=lib["H3_ALPN"])
            if not self.verify:
                config.verify_mode = ssl.CERT_NONE
            H3Connection = lib["H3Connection"]
            DataReceived = lib["DataReceived"]
            HeadersReceived = lib["HeadersReceived"]
            ConnectionTerminated = lib["ConnectionTerminated"]
            StreamReset = lib["StreamReset"]
            slots = asyncio.Semaphore(self.max_concurrent_streams)

            class Protocol(lib["QuicConnectionProtocol"]):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.http = H3Connection(self._quic)
                    self.pending = {}
                    self.dead = False
                    self.slots = slots

                async def request_stream(self, request_headers, body):
                    async with self.slots:
                        if self.dead:
                            raise ConnectionError("HTTP/3 connection is closed")
                        stream_id = self._quic.get_next_available_stream_id()
                        future = asyncio.get_running_loop().create_future()
                        self.pending[stream_id] = {"future": future, "headers": {}, "body": bytearray()}
                        try:
                            self.http.send_headers(stream_id, request_headers, end_stream=not body)
                            if body:
                                self.http.send_data(stream_id, body, end_stream=True)
                            self.transmit()
                            return await future
                        finally:
                            if self.pending.pop(stream_id, None) is not None:
                                # The caller gave up: cancel the stream so the server can free it.
                                try:
                                    self._quic.reset_stream(stream_id, H3_NO_ERROR)
                                    self.transmit()
                                except Exception:
                                    pass

                def _fail_all(self, message):
                    self.dead = True
                    for state in self.pending.values():
                        if not state["future"].done():
                            state["future"].set_exception(ConnectionError(message))
                    self.pending.clear()

                def quic_event_received(self, event):
                    if isinstance(event, ConnectionTerminated):
                        self._fail_all("HTTP/3 connection closed before stream ended")
                    elif isinstance(event, StreamReset):
                        state = self.pending.pop(event.stream_id, None)
                        if state is not None and not state["future"].done():
                            state["future"].set_exception(ConnectionError("HTTP/3 stream was reset"))
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
                            self.pending.pop(stream_id, None)
                            if not state["future"].done():
                                state["future"].set_result((
                                    int(state["headers"].get(":status", "0")),
                                    dict(state["headers"]),
                                    bytes(state["body"]),
                                ))

            stack = AsyncExitStack()
            try:
                protocol = await stack.enter_async_context(
                    _connect(lib, host, port, config, Protocol)
                )
            except BaseException:
                await stack.aclose()
                raise
            self._pool[key] = (protocol, stack)
            return protocol

    async def request(self, method, url, *, headers=None, data=b""):
        lib = _imports()
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("HTTP/3 requires an https:// URL")
        host, port = parts.hostname, parts.port or 443
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

        async def run():
            protocol = await self._protocol_for(lib, host, port)
            return await protocol.request_stream(request_headers, payload)

        return await asyncio.wait_for(run(), self.timeout)

    async def close(self):
        """Close every pooled connection."""
        entries = list(self._pool.values())
        self._pool.clear()
        for protocol, stack in entries:
            protocol.dead = True
            await stack.aclose()


class HTTP3Server:
    """HTTP/3 server. Handler: async handler(method, path, headers, body) -> (status, headers, body).

    Limits (each violation affects only the stream involved):
    - `max_concurrent_streams`: requests open at once on one connection. Over the limit, the
      new stream is reset with H3_EXCESSIVE_LOAD.
    - `max_header_list_size`: request headers larger than this are reset with H3_EXCESSIVE_LOAD.
    - `max_body_size`: larger bodies get 413.
    - `idle_timeout`: a QUIC connection with no traffic for this many seconds is closed.

    `close(grace=...)` stops new requests (they are refused with H3_REQUEST_REJECTED, which
    tells the client it is safe to retry), lets in-flight responses finish for up to `grace`
    seconds, closes the QUIC connections with NO_ERROR, then stops listening.
    """

    def __init__(
        self,
        handler,
        *,
        certificate,
        private_key,
        max_body_size=10 * 1024 * 1024,
        max_concurrent_streams=100,
        max_header_list_size=64 * 1024,
        idle_timeout=30.0,
    ):
        if max_concurrent_streams < 1:
            raise ValueError("max_concurrent_streams must be at least 1")
        if max_header_list_size < 1:
            raise ValueError("max_header_list_size must be at least 1")
        if idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive")
        self.handler = handler
        self.certificate = certificate
        self.private_key = private_key
        self.max_body_size = max_body_size
        self.max_concurrent_streams = max_concurrent_streams
        self.max_header_list_size = max_header_list_size
        self.idle_timeout = idle_timeout
        self._server = None
        self._tasks: set = set()  # keep references so responses are not garbage-collected
        self._protocols: set = set()
        self._draining = False

    async def start(self, host="0.0.0.0", port=443):
        lib = _imports()
        config = lib["QuicConfiguration"](
            is_client=False, alpn_protocols=lib["H3_ALPN"], idle_timeout=self.idle_timeout
        )
        config.load_cert_chain(self.certificate, self.private_key)
        server = self
        handler = self.handler
        max_body_size = self.max_body_size
        max_streams = self.max_concurrent_streams
        max_header_list_size = self.max_header_list_size
        H3Connection = lib["H3Connection"]
        DataReceived = lib["DataReceived"]
        HeadersReceived = lib["HeadersReceived"]
        ConnectionTerminated = lib["ConnectionTerminated"]

        class Protocol(lib["QuicConnectionProtocol"]):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.http = H3Connection(self._quic)
                self.requests = {}  # stream_id -> {"headers", "body"}, request still arriving
                self.open_streams = set()  # stream_id, from headers received until response sent
                server._protocols.add(self)

            def _reset(self, stream_id, error_code):
                self.open_streams.discard(stream_id)
                self.requests.pop(stream_id, None)
                try:
                    self._quic.reset_stream(stream_id, error_code)
                    self.transmit()
                except Exception:
                    pass

            def _spawn(self, stream_id):
                task = asyncio.ensure_future(self.respond(stream_id))
                server._tasks.add(task)
                task.add_done_callback(server._tasks.discard)

            def quic_event_received(self, event):
                if isinstance(event, ConnectionTerminated):
                    server._protocols.discard(self)
                for http_event in self.http.handle_event(event):
                    stream_id = getattr(http_event, "stream_id", None)
                    if stream_id is None:
                        continue
                    if isinstance(http_event, HeadersReceived):
                        if server._draining:
                            self._reset(stream_id, H3_REQUEST_REJECTED)
                            continue
                        if len(self.open_streams) >= max_streams:
                            self._reset(stream_id, H3_EXCESSIVE_LOAD)
                            continue
                        if _header_list_size(http_event.headers) > max_header_list_size:
                            self._reset(stream_id, H3_EXCESSIVE_LOAD)
                            continue
                        self.open_streams.add(stream_id)
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
                            self.open_streams.discard(stream_id)
                            self.transmit()
                        elif http_event.stream_ended:
                            self._spawn(stream_id)

            async def respond(self, stream_id):
                state = self.requests.pop(stream_id, None)
                if state is None:
                    self.open_streams.discard(stream_id)
                    return
                headers = state["headers"]
                try:
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
                    except Exception:
                        payload = b""
                        out = [(b":status", b"500"), (b"content-length", b"0")]
                    self.http.send_headers(stream_id, out, end_stream=not payload)
                    if payload:
                        self.http.send_data(stream_id, payload, end_stream=True)
                except Exception:
                    pass  # the stream or connection went away; nothing to send
                finally:
                    self.open_streams.discard(stream_id)
                    try:
                        self.transmit()
                    except Exception:
                        pass

        self._server = await lib["serve"](host, port, configuration=config, create_protocol=Protocol)
        return self._server

    @property
    def port(self) -> int:
        return self._server._transport.get_extra_info("sockname")[1]

    async def close(self, *, grace: float = 5.0):
        """Refuse new requests, let in-flight responses finish for up to `grace` seconds, then close."""
        self._draining = True
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=grace)
        for protocol in list(self._protocols):
            try:
                protocol.close(error_code=H3_NO_ERROR)
            except Exception:
                pass
        self._protocols.clear()
        for task in list(self._tasks):
            task.cancel()
        if self._server is not None:
            self._server.close()
            self._server = None


__all__ = ["HTTP3Client", "HTTP3Server"]
