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


class HTTP2Client:
    """One-request-per-connection HTTP/2 client with TLS ALPN negotiation."""

    def __init__(self, *, ssl_context=None, timeout: float = 15.0):
        self.ssl_context = ssl_context
        self.timeout = timeout

    async def get(self, url: str, *, headers=None):
        return await self.request("GET", url, headers=headers)

    async def request(self, method: str, url: str, *, headers=None, data=b""):
        from urllib.parse import urlsplit
        h2 = _require_h2()
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("HTTP/2 client currently requires an https:// URL")
        port = parts.port or 443
        context = self.ssl_context or ssl.create_default_context()
        context.set_alpn_protocols(["h2", "http/1.1"])
        async def exchange():
            reader, writer = await asyncio.open_connection(
                parts.hostname, port, ssl=context, server_hostname=parts.hostname
            )
            try:
                ssl_object = writer.get_extra_info("ssl_object")
                if ssl_object is None or ssl_object.selected_alpn_protocol() != "h2":
                    raise RuntimeError("server did not negotiate HTTP/2 via ALPN")
                config = h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
                conn = h2.connection.H2Connection(config=config)
                conn.initiate_connection()
                stream_id = conn.get_next_available_stream_id()
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                request_headers = [
                    (":method", method.upper()), (":scheme", "https"),
                    (":authority", parts.netloc), (":path", path),
                ]
                for key, value in (headers or {}).items():
                    if key.startswith(":") or key.lower() in ("connection", "transfer-encoding", "upgrade"):
                        raise ValueError("invalid HTTP/2 header")
                    request_headers.append((key.lower(), str(value)))
                conn.send_headers(stream_id, request_headers, end_stream=not data)
                if data:
                    conn.send_data(stream_id, data, end_stream=True)
                writer.write(conn.data_to_send())
                await writer.drain()
                response_headers, body = {}, bytearray()
                while True:
                    incoming = await reader.read(65536)
                    if not incoming:
                        raise ConnectionError("HTTP/2 connection closed before stream ended")
                    for event in conn.receive_data(incoming):
                        if isinstance(event, h2.events.ResponseReceived):
                            response_headers.update(event.headers)
                        elif isinstance(event, h2.events.DataReceived):
                            body.extend(event.data)
                            conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                        elif isinstance(event, h2.events.StreamEnded) and event.stream_id == stream_id:
                            writer.write(conn.data_to_send())
                            await writer.drain()
                            return int(response_headers.get(":status", "0")), response_headers, bytes(body)
                        elif isinstance(event, h2.events.StreamReset) and event.stream_id == stream_id:
                            raise ConnectionError("HTTP/2 stream was reset")
                    pending = conn.data_to_send()
                    if pending:
                        writer.write(pending)
                        await writer.drain()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
        return await asyncio.wait_for(exchange(), self.timeout)


class HTTP2Server:
    """Small HTTP/2-only TLS server.

    Handler signature: async handler(method, path, headers, body) -> (status, headers, body).
    """

    def __init__(self, handler, *, ssl_context, max_body_size=10 * 1024 * 1024):
        self.handler = handler
        self.ssl_context = ssl_context
        self.max_body_size = max_body_size
        self._server = None
        self.ssl_context.set_alpn_protocols(["h2"])

    async def _connection(self, reader, writer):
        h2 = _require_h2()
        config = h2.config.H2Configuration(client_side=False, header_encoding="utf-8")
        conn = h2.connection.H2Connection(config=config)
        streams = {}
        try:
            conn.initiate_connection()
            writer.write(conn.data_to_send())
            await writer.drain()
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                for event in conn.receive_data(data):
                    if isinstance(event, h2.events.RequestReceived):
                        streams[event.stream_id] = {"headers": dict(event.headers), "body": bytearray()}
                    elif isinstance(event, h2.events.DataReceived):
                        state = streams.get(event.stream_id)
                        if state is None:
                            continue
                        state["body"].extend(event.data)
                        conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                        if len(state["body"]) > self.max_body_size:
                            conn.reset_stream(event.stream_id, error_code=11)
                            streams.pop(event.stream_id, None)
                    elif isinstance(event, h2.events.StreamEnded):
                        state = streams.pop(event.stream_id, None)
                        if state is None:
                            continue
                        headers = state["headers"]
                        try:
                            result = await self.handler(
                                headers.get(":method", "GET"), headers.get(":path", "/"),
                                {k: v for k, v in headers.items() if not k.startswith(":")},
                                bytes(state["body"]),
                            )
                            status, response_headers, body = result
                            body = body.encode() if isinstance(body, str) else bytes(body)
                            out_headers = [(":status", str(int(status)))]
                            out_headers.extend((str(k).lower(), str(v)) for k, v in response_headers.items())
                            out_headers.append(("content-length", str(len(body))))
                            conn.send_headers(event.stream_id, out_headers, end_stream=not body)
                            if body:
                                conn.send_data(event.stream_id, body, end_stream=True)
                        except Exception:
                            conn.send_headers(event.stream_id, [(":status", "500"), ("content-length", "0")], end_stream=True)
                pending = conn.data_to_send()
                if pending:
                    writer.write(pending)
                    await writer.drain()
        except (ConnectionError, OSError, asyncio.IncompleteReadError, h2.exceptions.H2Error):
            # H2Error: malformed frames or protocol violations from the client.
            # Drop that connection only; never let it escape into the event loop.
            pass
        finally:
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

    async def close(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


__all__ = ["HTTP2Client", "HTTP2Server"]
