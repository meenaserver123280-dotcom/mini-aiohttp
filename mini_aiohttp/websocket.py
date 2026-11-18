"""Small RFC 6455 WebSocket client and server built on asyncio streams.

This module is deliberately independent of the HTTP/1.1 request parser: WebSocket
upgrades take ownership of a TCP connection after the HTTP handshake.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl as _ssl
import struct
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_FRAME = 16 * 1024 * 1024
_MAX_HEADERS = 100
_VALID_CLOSE_CODES = set(range(1000, 1015)) - {1004, 1005, 1006}
_VALID_CLOSE_CODES.update(range(3000, 5000))


class WebSocketError(Exception):
    """Raised for invalid handshakes, frames, or closed connections."""


def _accept_value(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")


async def _read_exact(reader, size: int) -> bytes:
    try:
        return await reader.readexactly(size)
    except asyncio.IncompleteReadError as exc:
        raise WebSocketError("connection closed during frame") from exc
    except (ConnectionError, OSError) as exc:
        raise WebSocketError("connection lost") from exc


class WebSocket:
    """A connected WebSocket. Use connect() for clients or accept() on server streams."""

    def __init__(self, reader, writer, *, client: bool):
        self.reader = reader
        self.writer = writer
        self.client = client
        self.closed = False
        self._send_lock = asyncio.Lock()

    @classmethod
    async def connect(cls, url: str, *, headers: dict | None = None,
                      ssl_context=None, timeout: float = 10.0) -> "WebSocket":
        """Connect to ws:// or wss:// and validate the RFC 6455 upgrade."""
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise ValueError("WebSocket URL must use ws:// or wss://")
        secure = parts.scheme == "wss"
        port = parts.port or (443 if secure else 80)
        context = ssl_context or (_ssl.create_default_context() if secure else None)

        async def handshake():
            reader, writer = await asyncio.open_connection(
                parts.hostname, port, ssl=context,
                server_hostname=parts.hostname if secure else None,
            )
            try:
                key = base64.b64encode(os.urandom(16)).decode("ascii")
                target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
                fields = {
                    "Host": parts.netloc, "Upgrade": "websocket",
                    "Connection": "Upgrade", "Sec-WebSocket-Key": key,
                    "Sec-WebSocket-Version": "13",
                }
                reserved = {name.lower() for name in fields}
                for name, value in (headers or {}).items():
                    if name.lower() in reserved:
                        raise ValueError("WebSocket handshake headers cannot be overridden")
                    if any(ch in str(name) + str(value) for ch in "\r\n"):
                        raise ValueError("invalid WebSocket header")
                    fields[name] = value
                request = [f"GET {target} HTTP/1.1"] + [f"{k}: {v}" for k, v in fields.items()]
                writer.write(("\r\n".join(request) + "\r\n\r\n").encode("latin-1"))
                await writer.drain()
                status = await reader.readline()
                if not status.startswith(b"HTTP/1.1 101 "):
                    raise WebSocketError("server rejected WebSocket upgrade")
                response_headers = {}
                while True:
                    line = await reader.readline()
                    if line in (b"\r\n", b"\n", b""):
                        break
                    name, sep, value = line.decode("latin-1").partition(":")
                    if not sep:
                        raise WebSocketError("malformed upgrade response")
                    response_headers[name.strip().lower()] = value.strip()
                if response_headers.get("upgrade", "").lower() != "websocket":
                    raise WebSocketError("missing Upgrade: websocket")
                if "upgrade" not in response_headers.get("connection", "").lower():
                    raise WebSocketError("missing Connection: Upgrade")
                if response_headers.get("sec-websocket-accept") != _accept_value(key):
                    raise WebSocketError("invalid Sec-WebSocket-Accept")
                return cls(reader, writer, client=True)
            except BaseException:
                # Covers WebSocketError, ValueError, and asyncio.CancelledError from
                # the timeout below: never leave a half-open socket behind.
                writer.close()
                raise

        return await asyncio.wait_for(handshake(), timeout)

    @classmethod
    async def accept(cls, reader, writer, request_headers: dict) -> "WebSocket":
        """Validate a parsed upgrade request and return a server-side connection."""
        if "host" not in request_headers:
            raise WebSocketError("missing Host header")
        key = request_headers.get("sec-websocket-key", "")
        try:
            decoded = base64.b64decode(key, validate=True)
        except Exception as exc:
            raise WebSocketError("invalid Sec-WebSocket-Key") from exc
        if len(decoded) != 16 or request_headers.get("sec-websocket-version") != "13":
            raise WebSocketError("unsupported WebSocket handshake")
        if request_headers.get("upgrade", "").lower() != "websocket":
            raise WebSocketError("missing Upgrade: websocket")
        if "upgrade" not in request_headers.get("connection", "").lower():
            raise WebSocketError("missing Connection: Upgrade")
        writer.write((
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {_accept_value(key)}\r\n\r\n"
        ).encode("ascii"))
        await writer.drain()
        return cls(reader, writer, client=False)

    async def send(self, data: str | bytes) -> None:
        """Send a UTF-8 text message or binary message."""
        if self.closed:
            raise WebSocketError("WebSocket is closed")
        if not isinstance(data, (str, bytes, bytearray, memoryview)):
            raise TypeError("WebSocket messages must be str or bytes-like")
        opcode = 1 if isinstance(data, str) else 2
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        await self._send_frame(opcode, payload)

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        if len(payload) > _MAX_FRAME:
            raise WebSocketError("frame exceeds configured maximum")
        mask = self.client
        first = bytes((0x80 | opcode,))
        marker = 0x80 if mask else 0
        length = len(payload)
        if length < 126:
            header = first + bytes((marker | length,))
        elif length <= 0xFFFF:
            header = first + bytes((marker | 126,)) + struct.pack("!H", length)
        else:
            header = first + bytes((marker | 127,)) + struct.pack("!Q", length)
        if mask:
            key = os.urandom(4)
            payload = bytes(byte ^ key[i & 3] for i, byte in enumerate(payload))
            header += key
        async with self._send_lock:
            try:
                self.writer.write(header + payload)
                await self.writer.drain()
            except (ConnectionError, OSError) as exc:
                raise WebSocketError("connection lost while sending") from exc

    async def recv(self) -> str | bytes | None:
        """Receive one complete message. Returns None after a close frame."""
        fragments = bytearray()
        message_opcode = None
        while not self.closed:
            head = await _read_exact(self.reader, 2)
            fin, opcode = bool(head[0] & 0x80), head[0] & 0x0F
            masked = bool(head[1] & 0x80)
            length = head[1] & 0x7F
            if (head[0] & 0x70) or (self.client and masked) or (not self.client and not masked):
                raise WebSocketError("invalid frame flags or masking")
            if opcode not in (0, 1, 2, 8, 9, 10):
                raise WebSocketError("reserved WebSocket opcode")
            if length == 126:
                length = struct.unpack("!H", await _read_exact(self.reader, 2))[0]
                if length < 126:
                    raise WebSocketError("non-minimal frame length")
            elif length == 127:
                length = struct.unpack("!Q", await _read_exact(self.reader, 8))[0]
                if length < 65536 or length >> 63:
                    raise WebSocketError("invalid or non-minimal frame length")
            if length > _MAX_FRAME:
                raise WebSocketError("frame exceeds configured maximum")
            if opcode >= 8 and (not fin or length > 125):
                raise WebSocketError("invalid control frame")
            mask_key = await _read_exact(self.reader, 4) if masked else b""
            payload = await _read_exact(self.reader, length)
            if masked:
                payload = bytes(byte ^ mask_key[i & 3] for i, byte in enumerate(payload))
            if opcode >= 8:
                if opcode == 8:
                    if length == 1:
                        raise WebSocketError("invalid close payload")
                    if length >= 2:
                        code = struct.unpack("!H", payload[:2])[0]
                        if code not in _VALID_CLOSE_CODES:
                            raise WebSocketError("invalid WebSocket close code")
                        try:
                            payload[2:].decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise WebSocketError("invalid close reason UTF-8") from exc
                    try:
                        await self._send_frame(8, payload)
                    except WebSocketError:
                        pass  # peer is already gone; we still report the close below
                    self.closed = True
                    self.writer.close()
                    return None
                if opcode == 9:
                    try:
                        await self._send_frame(10, payload)
                    except WebSocketError:
                        pass
                continue
            if opcode in (1, 2):
                if message_opcode is not None:
                    raise WebSocketError("new data frame before fragmented message ended")
                message_opcode = opcode
            elif opcode != 0 or message_opcode is None:
                raise WebSocketError("invalid continuation frame")
            fragments.extend(payload)
            if len(fragments) > _MAX_FRAME:
                raise WebSocketError("message exceeds configured maximum")
            if fin:
                if message_opcode == 1:
                    try:
                        return fragments.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise WebSocketError("invalid UTF-8 text message") from exc
                return bytes(fragments)
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        if code not in _VALID_CLOSE_CODES:
            raise ValueError("invalid WebSocket close code")
        reason_bytes = reason.encode("utf-8")
        if len(reason_bytes) > 123:
            raise ValueError("WebSocket close reason must be at most 123 UTF-8 bytes")
        payload = struct.pack("!H", code) + reason_bytes
        try:
            await self._send_frame(8, payload)
        except WebSocketError:
            pass
        self.closed = True
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass


class WebSocketServer:
    """Minimal standalone WebSocket server. Handler: async def handler(websocket, path)."""
    def __init__(self, handler, *, ssl=None, max_connections: int = 1000):
        self.handler = handler
        self.ssl = ssl
        self.max_connections = max_connections
        self._server = None
        self._active = 0
        self._tasks = set()
        self._writers = set()

    async def _accept_connection(self, reader, writer):
        task = asyncio.current_task()
        self._tasks.add(task)
        if self._active >= self.max_connections:
            writer.close()
            self._tasks.discard(task)
            return
        self._active += 1
        self._writers.add(writer)
        websocket = None
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            parts = line.decode("latin-1").strip().split(" ")
            if len(parts) != 3 or parts[0] != "GET" or parts[2] != "HTTP/1.1":
                raise WebSocketError("invalid WebSocket request line")
            headers = {}
            while True:
                raw = await asyncio.wait_for(reader.readline(), 10)
                if raw in (b"\r\n", b"\n", b""):
                    break
                if len(headers) >= _MAX_HEADERS:
                    raise WebSocketError("too many request headers")
                name, sep, value = raw.decode("latin-1").partition(":")
                if not sep:
                    raise WebSocketError("malformed request header")
                key = name.strip().lower()
                if key in headers:
                    raise WebSocketError("duplicate request header")
                headers[key] = value.strip()
            websocket = await WebSocket.accept(reader, writer, headers)
            await self.handler(websocket, parts[1])
        except (WebSocketError, asyncio.TimeoutError, ConnectionError, OSError, ValueError):
            # ValueError: a request line or header longer than the StreamReader limit.
            pass
        finally:
            if websocket is not None and not websocket.closed:
                try:
                    await websocket.close()
                except Exception:
                    writer.close()
            else:
                writer.close()
            self._active -= 1
            self._tasks.discard(task)
            self._writers.discard(writer)

    async def start(self, host: str = "127.0.0.1", port: int = 8765):
        self._server = await asyncio.start_server(
            self._accept_connection, host, port, ssl=self.ssl
        )
        return self._server

    async def close(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._writers):
            writer.close()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


__all__ = ["WebSocket", "WebSocketServer", "WebSocketError"]
