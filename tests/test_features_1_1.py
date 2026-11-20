"""Tests for 1.1 changes: multi-range static responses, WebSocket permessage-deflate, and the AI chat helper."""
from __future__ import annotations

import pytest

from mini_aiohttp import WebSocket, WebSocketServer
from mini_aiohttp.ai import ChatClient
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

def test_chat_client_validates_arguments():
    with pytest.raises(ValueError):
        ChatClient(None, base_url="ftp://example.com", api_key=None, model="m")
    with pytest.raises(ValueError):
        ChatClient(None, base_url="https://example.com", api_key=None, model="")
