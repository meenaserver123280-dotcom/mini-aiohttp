"""Optional HTTP/2 client and server adapters.

Install with: pip install mini-aiohttp[http2]
The adapters use hyper-h2 and asyncio TLS ALPN. They are intentionally separate
from the HTTP/1.1 classes so the dependency-free core remains small.
"""
from __future__ import annotations

import asyncio
import ssl


def _require_h2():
    try:
        import h2.config
        import h2.connection
        import h2.events
        import h2.exceptions
    except ImportError as exc:
        raise RuntimeError("HTTP/2 support requires 'pip install mini-aiohttp[http2]'") from exc
    return h2


class _Stream:
    """One client-side stream: its response future and the bytes received so far."""

    __slots__ = ("future", "headers", "body")

    def __init__(self, future):
        self.future = future
        self.headers: dict = {}
        self.body = bytearray()


class _ClientConnection:
    """One TLS connection to an origin, carrying many concurrent streams.

    A background task reads frames and resolves each stream's future. Sends are
    serialized with a lock. New streams wait while the peer's
    MAX_CONCURRENT_STREAMS is reached.
    """

    def __init__(self, h2, reader, writer):
        self.h2 = h2
        self.reader = reader
        self.writer = writer
        config = h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
        self.conn = h2.connection.H2Connection(config=config)
        self.streams: dict = {}
        self.closed = False
        self._write_lock = asyncio.Lock()
        self._slot_freed = asyncio.Event()
        # Requests wait until the server's SETTINGS arrive, so the stream limit is known
        # before the first request is sent. Sending earlier would break that limit.
        self._settings_ready = asyncio.Event()
        self._reader_task = None

    async def start(self):
        self.conn.initiate_connection()
        await self._flush()
        self._reader_task = asyncio.ensure_future(self._read_loop())

    async def _flush(self):
        async with self._write_lock:
            pending = self.conn.data_to_send()
            if pending:
                self.writer.write(pending)
                await self.writer.drain()

    async def _flush_quiet(self):
        try:
            await self._flush()
        except Exception:
            pass

    def _limit(self) -> int:
        return self.conn.remote_settings.max_concurrent_streams

    async def request(self, method, path, authority, headers, data):
        loop = asyncio.get_running_loop()
        stream = _Stream(loop.create_future())
        await self._settings_ready.wait()
        while True:
            async with self._write_lock:
                if self.closed:
                    raise ConnectionError("HTTP/2 connection is closed")
                if len(self.streams) < self._limit():
                    sid = self.conn.get_next_available_stream_id()
                    request_headers = [
                        (":method", method), (":scheme", "https"),
                        (":authority", authority), (":path", path),
                    ]
                    request_headers.extend(headers)
                    self.conn.send_headers(sid, request_headers, end_stream=not data)
                    if data:
                        self.conn.send_data(sid, data, end_stream=True)
                    self.streams[sid] = stream
                    self.writer.write(self.conn.data_to_send())
                    await self.writer.drain()
                    break
                self._slot_freed.clear()
            # No await between the check above and clear(), so no slot can be missed.
            await self._slot_freed.wait()
        try:
            return await stream.future
        finally:
            if self.streams.pop(sid, None) is not None:
                # The caller gave up (timeout or cancel) before the response ended.
                self._slot_freed.set()
                try:
                    self.conn.reset_stream(sid, error_code=8)  # CANCEL
                except Exception:
                    pass
                asyncio.ensure_future(self._flush_quiet())

    async def _read_loop(self):
        h2 = self.h2
        try:
            while True:
                incoming = await self.reader.read(65536)
                if not incoming:
                    break
                for event in self.conn.receive_data(incoming):
                    if isinstance(event, h2.events.ResponseReceived):
                        stream = self.streams.get(event.stream_id)
                        if stream is not None:
                            stream.headers.update(event.headers)
                    elif isinstance(event, h2.events.DataReceived):
                        stream = self.streams.get(event.stream_id)
                        if stream is not None:
                            stream.body.extend(event.data)
                        self.conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                    elif isinstance(event, h2.events.StreamEnded):
                        stream = self.streams.pop(event.stream_id, None)
                        if stream is not None:
                            self._slot_freed.set()
                            status = int(stream.headers.get(":status", "0"))
                            if not stream.future.done():
                                stream.future.set_result((status, stream.headers, bytes(stream.body)))
                    elif isinstance(event, h2.events.StreamReset):
                        stream = self.streams.pop(event.stream_id, None)
                        if stream is not None:
                            self._slot_freed.set()
                            if not stream.future.done():
                                stream.future.set_exception(ConnectionError("HTTP/2 stream was reset"))
                    elif isinstance(event, h2.events.RemoteSettingsChanged):
                        self._settings_ready.set()
                        self._slot_freed.set()
                    elif isinstance(event, h2.events.ConnectionTerminated):
                        raise ConnectionError("HTTP/2 connection terminated by server")
                await self._flush()
        except (ConnectionError, OSError, h2.exceptions.H2Error):
            pass
        finally:
            self.closed = True
            self._slot_freed.set()
            self._settings_ready.set()
            for stream in self.streams.values():
                if not stream.future.done():
                    stream.future.set_exception(ConnectionError("HTTP/2 connection closed before stream ended"))
            self.streams.clear()
            self.writer.close()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.conn.close_connection(error_code=0)
            await self._flush()
        except Exception:
            pass
        if self._reader_task is not None:
            self._reader_task.cancel()
        self.writer.close()


class HTTP2Client:
    """HTTP/2 client with TLS ALPN, one pooled connection per origin, and parallel streams.

    Requests to the same origin share a connection. When the server's
    MAX_CONCURRENT_STREAMS is reached, extra requests wait for a free slot.
    Call `await client.close()` to close the pooled connections.
    """

    def __init__(self, *, ssl_context=None, timeout: float = 15.0):
        self.ssl_context = ssl_context
        self.timeout = timeout
        self._connections: dict = {}
        self._connect_locks: dict = {}

    async def get(self, url: str, *, headers=None):
        return await self.request("GET", url, headers=headers)

    async def _connection_for(self, host, port, context):
        key = (host, port)
        conn = self._connections.get(key)
        if conn is not None and not conn.closed:
            return conn
        lock = self._connect_locks.setdefault(key, asyncio.Lock())
        async with lock:
            conn = self._connections.get(key)
            if conn is not None and not conn.closed:
                return conn
            h2 = _require_h2()
            reader, writer = await asyncio.open_connection(host, port, ssl=context, server_hostname=host)
            try:
                ssl_object = writer.get_extra_info("ssl_object")
                if ssl_object is None or ssl_object.selected_alpn_protocol() != "h2":
                    raise RuntimeError("server did not negotiate HTTP/2 via ALPN")
                conn = _ClientConnection(h2, reader, writer)
                await conn.start()
            except BaseException:
                writer.close()
                raise
            self._connections[key] = conn
            return conn

    async def request(self, method: str, url: str, *, headers=None, data=b""):
        from urllib.parse import urlsplit
        _require_h2()
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("HTTP/2 client currently requires an https:// URL")
        port = parts.port or 443
        context = self.ssl_context or ssl.create_default_context()
        context.set_alpn_protocols(["h2", "http/1.1"])
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        extra = []
        for key, value in (headers or {}).items():
            if key.startswith(":") or key.lower() in ("connection", "transfer-encoding", "upgrade"):
                raise ValueError("invalid HTTP/2 header")
            extra.append((key.lower(), str(value)))
        conn = await asyncio.wait_for(self._connection_for(parts.hostname, port, context), self.timeout)
        status, response_headers, body = await asyncio.wait_for(
            conn.request(method.upper(), path, parts.netloc, extra, data), self.timeout
        )
        return status, response_headers, body

    async def close(self):
        """Close every pooled connection."""
        for conn in list(self._connections.values()):
            await conn.close()
        self._connections.clear()


class _Connection:
    """Server-side state for one HTTP/2 connection, used for shutdown and tests."""

    def __init__(self, conn, writer, flush):
        self.conn = conn
        self.writer = writer
        self.flush = flush
        self.tasks: set = set()


class HTTP2Server:
    """HTTP/2-only TLS server with concurrent streams, limits and graceful shutdown.

    Handler signature: async handler(method, path, headers, body) -> (status, headers, body).

    Behaviour:
    - Each stream's handler runs in its own task, so a slow stream does not block others.
    - `max_concurrent_streams` is advertised and enforced. A peer that exceeds it is a
      protocol error: the connection gets GOAWAY(PROTOCOL_ERROR) and is closed.
    - `max_header_list_size` limits the size of request headers. Larger requests are
      refused with a connection error (RFC 9113, section 6.5.2).
    - A stream whose body exceeds `max_body_size` is reset (ENHANCE_YOUR_CALM), and other
      streams on the connection continue.
    - A connection with no bytes received for `idle_timeout` seconds gets GOAWAY(NO_ERROR)
      and is closed.
    - `close(grace=...)` stops accepting, sends GOAWAY(NO_ERROR) on every open connection,
      lets in-flight streams finish for up to `grace` seconds, then closes what is left.
      Requests that arrive after GOAWAY are refused with REFUSED_STREAM.
    """

    def __init__(
        self,
        handler,
        *,
        ssl_context,
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
        self.ssl_context = ssl_context
        self.max_body_size = max_body_size
        self.max_concurrent_streams = max_concurrent_streams
        self.max_header_list_size = max_header_list_size
        self.idle_timeout = idle_timeout
        self._server = None
        self._connections: set = set()
        self._draining = False
        self.ssl_context.set_alpn_protocols(["h2"])

    async def _connection(self, reader, writer):
        h2 = _require_h2()
        from h2.settings import Settings, SettingCodes

        if self._draining:
            writer.close()
            return
        config = h2.config.H2Configuration(client_side=False, header_encoding="utf-8")
        conn = h2.connection.H2Connection(config=config)
        conn.local_settings = Settings(
            client=False,
            initial_values={
                SettingCodes.MAX_CONCURRENT_STREAMS: self.max_concurrent_streams,
                SettingCodes.MAX_HEADER_LIST_SIZE: self.max_header_list_size,
            },
        )
        # asyncio's drain() cannot run concurrently, and several stream tasks may flush.
        flush_lock = asyncio.Lock()

        async def flush():
            async with flush_lock:
                pending = conn.data_to_send()
                if pending:
                    writer.write(pending)
                    await writer.drain()

        state = _Connection(conn, writer, flush)
        self._connections.add(state)
        streams: dict = {}  # stream_id -> {"headers": ..., "body": bytearray}

        async def answer(stream_id, headers, body):
            method = headers.get(":method", "GET")
            path = headers.get(":path", "/")
            plain = {k: v for k, v in headers.items() if not k.startswith(":")}
            try:
                status, response_headers, out = await self.handler(method, path, plain, body)
                out = out.encode() if isinstance(out, str) else bytes(out)
                out_headers = [(":status", str(int(status)))]
                out_headers.extend((str(k).lower(), str(v)) for k, v in response_headers.items())
                out_headers.append(("content-length", str(len(out))))
            except Exception:
                out, out_headers = b"", [(":status", "500"), ("content-length", "0")]
            try:
                conn.send_headers(stream_id, out_headers, end_stream=not out)
                if out:
                    conn.send_data(stream_id, out, end_stream=True)
            except h2.exceptions.H2Error:
                return  # the stream was reset by the client or the connection is closing
            await flush()

        def start_answer(stream_id, headers, body):
            task = asyncio.ensure_future(answer(stream_id, headers, body))
            state.tasks.add(task)
            task.add_done_callback(state.tasks.discard)

        try:
            conn.initiate_connection()
            await flush()
            while True:
                try:
                    async with asyncio.timeout(self.idle_timeout):
                        data = await reader.read(65536)
                except TimeoutError:
                    conn.close_connection(error_code=0)  # NO_ERROR: idle for too long
                    await flush()
                    return
                if not data:
                    return
                for event in conn.receive_data(data):
                    if isinstance(event, h2.events.RequestReceived):
                        if self._draining:
                            conn.reset_stream(event.stream_id, error_code=7)  # REFUSED_STREAM
                            continue
                        # RFC 9113 6.5.2: size of a header list is name + value + 32 bytes per field.
                        header_size = sum(len(k) + len(v) + 32 for k, v in event.headers)
                        if header_size > self.max_header_list_size:
                            conn.reset_stream(event.stream_id, error_code=11)  # ENHANCE_YOUR_CALM
                            continue
                        streams[event.stream_id] = {"headers": dict(event.headers), "body": bytearray()}
                    elif isinstance(event, h2.events.DataReceived):
                        stream = streams.get(event.stream_id)
                        if stream is None:
                            continue
                        stream["body"].extend(event.data)
                        conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                        if len(stream["body"]) > self.max_body_size:
                            conn.reset_stream(event.stream_id, error_code=11)  # ENHANCE_YOUR_CALM
                            streams.pop(event.stream_id, None)
                    elif isinstance(event, h2.events.StreamEnded):
                        stream = streams.pop(event.stream_id, None)
                        if stream is None:
                            continue
                        start_answer(event.stream_id, stream["headers"], bytes(stream["body"]))
                    elif isinstance(event, h2.events.StreamReset):
                        streams.pop(event.stream_id, None)
                await flush()
        except h2.exceptions.H2Error:
            # Malformed frames, oversized headers or limit violations: tell the client why,
            # then drop only this connection. Never let it reach the event loop.
            try:
                conn.close_connection(error_code=1)  # PROTOCOL_ERROR
                await flush()
            except (h2.exceptions.H2Error, ConnectionError, OSError):
                pass
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self._connections.discard(state)
            # Let streams that are still running finish when we are draining; cancel otherwise.
            if not self._draining:
                for task in list(state.tasks):
                    task.cancel()
            if state.tasks:
                await asyncio.gather(*list(state.tasks), return_exceptions=True)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def start(self, host="127.0.0.1", port=8443):
        self._server = await asyncio.start_server(
            self._connection, host, port, ssl=self.ssl_context
        )
        return self._server

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def close(self, *, grace: float = 5.0):
        """Stop accepting, send GOAWAY(NO_ERROR) to open connections, wait up to `grace`, then close."""
        self._draining = True
        if self._server is not None:
            self._server.close()
        # Let in-flight streams finish first. h2 forbids sending on a connection after
        # GOAWAY, so GOAWAY is sent only once the responses are out.
        tasks = set().union(*(state.tasks for state in self._connections)) if self._connections else set()
        if tasks:
            await asyncio.wait(tasks, timeout=grace)
        for state in list(self._connections):
            try:
                state.conn.close_connection(error_code=0)  # NO_ERROR
                await state.flush()
            except Exception:
                pass
        for state in list(self._connections):
            for task in list(state.tasks):
                task.cancel()
            state.writer.close()
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None


__all__ = ["HTTP2Client", "HTTP2Server"]
