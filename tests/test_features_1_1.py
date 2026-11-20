"""Tests for 1.1 changes: multi-range static responses, WebSocket permessage-deflate, and the AI chat helper."""
from __future__ import annotations

import json

import pytest

from mini_aiohttp import WebSocket, WebSocketServer
from mini_aiohttp.ai import AIError, ChatClient
from mini_aiohttp.static import _byte_ranges

ETAG = '"e"'
LAST_MODIFIED = "lm"


# ---------- static multi-range ----------

def test_multiple_ranges_are_returned_together():
    headers = {"range": "bytes=0-1,5-"}
    assert _byte_ranges(headers, ETAG, LAST_MODIFIED, 10) == [(0, 1), (5, 9)]


def test_suffix_range():
    assert _byte_ranges({"range": "bytes=-3"}, ETAG, LAST_MODIFIED, 10) == [(7, 9)]


def test_unsatisfiable_range():
    result = _byte_ranges({"range": "bytes=20-30"}, ETAG, LAST_MODIFIED, 10)
    assert isinstance(result, str)


def test_malformed_range_is_ignored():
    assert _byte_ranges({"range": "bytes=5-1"}, ETAG, LAST_MODIFIED, 10) is None


def test_if_range_mismatch_sends_whole_file():
    headers = {"range": "bytes=0-1", "if-range": "nope"}
    assert _byte_ranges(headers, ETAG, LAST_MODIFIED, 10) is None


# ---------- WebSocket permessage-deflate ----------

async def _echo_server():
    async def echo(ws, path):
        message = await ws.recv()
        await ws.send(message)

    server = WebSocketServer(echo)
    listening = await server.start("127.0.0.1", 0)
    port = listening.sockets[0].getsockname()[1]
    return server, port


async def test_permessage_deflate_round_trip():
    server, port = await _echo_server()
    try:
        ws = await WebSocket.connect(f"ws://127.0.0.1:{port}/echo")
        assert ws._deflate is True
        text = "hello " * 200
        await ws.send(text)
        assert await ws.recv() == text
        await ws.close()
    finally:
        await server.close()


async def test_compression_can_be_disabled():
    server, port = await _echo_server()
    try:
        ws = await WebSocket.connect(f"ws://127.0.0.1:{port}/echo", compression=False)
        assert ws._deflate is False
        await ws.send("plain")
        assert await ws.recv() == "plain"
        await ws.close()
    finally:
        await server.close()


# ---------- AI chat helper ----------

class _FakeResponse:
    def __init__(self, status, chunks=(), body=b""):
        self.status = status
        self._chunks = list(chunks)
        self.body = body
        self.closed = False

    async def iter_chunks(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return self.body

    async def aclose(self):
        self.closed = True

    def json(self):
        return json.loads(self.body)


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.sent = []

    async def request(self, method, url, **kwargs):
        self.sent.append(kwargs)
        return self.response


def _client(session):
    return ChatClient(session, base_url="https://example.com/v1", api_key="k", model="m")


def _delta(text):
    return json.dumps({"choices": [{"delta": {"content": text}}]}).encode()


def test_chat_client_validates_arguments():
    with pytest.raises(ValueError):
        ChatClient(None, base_url="ftp://example.com", api_key=None, model="m")
    with pytest.raises(ValueError):
        ChatClient(None, base_url="https://example.com", api_key=None, model="")


async def test_chat_returns_reply_text():
    body = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()
    session = _FakeSession(_FakeResponse(200, body=body))
    assert await _client(session).chat([{"role": "user", "content": "x"}]) == "hi"
    assert session.sent[0]["json"]["model"] == "m"
    assert session.sent[0]["json"]["stream"] is False


async def test_stream_yields_pieces_in_order():
    chunks = [b"data: " + _delta("he") + b"\n\n", b"data: " + _delta("llo") + b"\n\n", b"data: [DONE]\n\n"]
    session = _FakeSession(_FakeResponse(200, chunks=chunks))
    pieces = [p async for p in _client(session).stream([{"role": "user", "content": "x"}])]
    assert pieces == ["he", "llo"]
    assert session.sent[0]["json"]["stream"] is True


async def test_stream_keeps_final_event_without_newline():
    chunks = [b"data: " + _delta("he") + b"\n", b"data: " + _delta("llo")]
    session = _FakeSession(_FakeResponse(200, chunks=chunks))
    pieces = [p async for p in _client(session).stream([{"role": "user", "content": "x"}])]
    assert pieces == ["he", "llo"]


async def test_stream_stops_at_done_marker():
    chunks = [b"data: " + _delta("a") + b"\n", b"data: [DONE]\n", b"data: " + _delta("ignored") + b"\n"]
    session = _FakeSession(_FakeResponse(200, chunks=chunks))
    pieces = [p async for p in _client(session).stream([{"role": "user", "content": "x"}])]
    assert pieces == ["a"]


async def test_stream_error_status_raises_ai_error():
    body = json.dumps({"error": {"message": "bad key"}}).encode()
    session = _FakeSession(_FakeResponse(401, body=body))
    with pytest.raises(AIError) as info:
        async for _ in _client(session).stream([{"role": "user", "content": "x"}]):
            pass
    assert info.value.status == 401
    assert "bad key" in info.value.message


@pytest.mark.parametrize("name", ["model", "stream"])
async def test_reserved_options_are_rejected(name):
    session = _FakeSession(_FakeResponse(200))
    with pytest.raises(ValueError):
        await _client(session).chat([{"role": "user", "content": "x"}], **{name: "override"})
    assert session.sent == []


async def test_messages_option_clashes_with_argument():
    # messages is positional in chat(), so passing it again as a keyword is a
    # duplicate argument. Python raises TypeError before any request is sent.
    session = _FakeSession(_FakeResponse(200))
    with pytest.raises(TypeError):
        await _client(session).chat([{"role": "user", "content": "x"}], messages="override")
    assert session.sent == []


async def test_reserved_options_are_rejected_for_stream():
    session = _FakeSession(_FakeResponse(200))
    with pytest.raises(ValueError):
        async for _ in _client(session).stream([{"role": "user", "content": "x"}], model="other"):
            pass
    assert session.sent == []
