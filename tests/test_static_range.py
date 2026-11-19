"""StaticFiles middleware: Range, ETag, conditional GET and path safety."""
from __future__ import annotations

from email.utils import formatdate

import pytest

from mini_aiohttp import Request, Response
from mini_aiohttp.static import StaticFiles

CONTENT = b"0123456789abcdefghij"  # 20 bytes


@pytest.fixture
def root(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    (public / "data.txt").write_bytes(CONTENT)
    (public / "empty.txt").write_bytes(b"")
    (tmp_path / "secret.txt").write_text("do not serve")
    return public


async def _next(req):
    return Response(404, "from next")


def _request(path, method="GET", **headers):
    return Request(method, path, "", "HTTP/1.1", {k.lower().replace("_", "-"): v for k, v in headers.items()}, b"")


async def _call(static, req):
    return await static(req, _next)


async def _body(resp):
    if resp.is_streaming:
        parts = []
        async for chunk in resp._out_chunks:
            parts.append(chunk)
        return b"".join(parts)
    return resp.body


async def _get(root, path, **headers):
    static = StaticFiles("/static", root)
    resp = await _call(static, _request(path, **headers))
    return resp, await _body(resp)


async def test_full_get_returns_whole_file_with_validators(root):
    resp, body = await _get(root, "/static/data.txt")
    assert resp.status == 200
    assert body == CONTENT
    assert resp._known_length == len(CONTENT)
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["etag"].startswith('"') and resp.headers["etag"].endswith('"')
    assert resp.headers["content-type"] == "text/plain"
    assert "last-modified" in resp.headers


async def test_head_has_headers_but_no_body(root):
    static = StaticFiles("/static", root)
    resp = await _call(static, _request("/static/data.txt", method="HEAD"))
    assert resp.status == 200
    assert resp.body == b""
    assert resp._known_length == len(CONTENT)


async def test_explicit_range_returns_206(root):
    resp, body = await _get(root, "/static/data.txt", range="bytes=0-4")
    assert resp.status == 206
    assert body == b"01234"
    assert resp.headers["content-range"] == f"bytes 0-4/{len(CONTENT)}"
    assert resp._known_length == 5


async def test_open_ended_range(root):
    resp, body = await _get(root, "/static/data.txt", range="bytes=15-")
    assert resp.status == 206
    assert body == b"fghij"
    assert resp.headers["content-range"] == "bytes 15-19/20"


async def test_suffix_range(root):
    resp, body = await _get(root, "/static/data.txt", range="bytes=-3")
    assert resp.status == 206
    assert body == b"hij"
    assert resp.headers["content-range"] == "bytes 17-19/20"


async def test_end_past_file_is_clamped(root):
    resp, body = await _get(root, "/static/data.txt", range="bytes=18-999")
    assert resp.status == 206
    assert body == b"ij"


async def test_unsatisfiable_range_returns_416(root):
    resp, _ = await _get(root, "/static/data.txt", range="bytes=999-")
    assert resp.status == 416
    assert resp.headers["content-range"] == "bytes */20"


async def test_zero_length_suffix_is_unsatisfiable(root):
    resp, _ = await _get(root, "/static/data.txt", range="bytes=-0")
    assert resp.status == 416


async def test_range_on_empty_file_is_unsatisfiable(root):
    resp, _ = await _get(root, "/static/empty.txt", range="bytes=0-0")
    assert resp.status == 416


async def test_malformed_range_is_ignored(root):
    resp, body = await _get(root, "/static/data.txt", range="items=0-4")
    assert resp.status == 200
    assert body == CONTENT


async def test_multi_range_is_ignored(root):
    resp, body = await _get(root, "/static/data.txt", range="bytes=0-1,5-6")
    assert resp.status == 200
    assert body == CONTENT


async def test_if_range_with_matching_etag_keeps_partial(root):
    first, _ = await _get(root, "/static/data.txt")
    resp, body = await _get(root, "/static/data.txt", range="bytes=0-4", **{"if-range": first.headers["etag"]})
    assert resp.status == 206
    assert body == b"01234"


async def test_if_range_with_stale_etag_sends_whole_file(root):
    resp, body = await _get(root, "/static/data.txt", range="bytes=0-4", **{"if-range": '"stale"'})
    assert resp.status == 200
    assert body == CONTENT


async def test_matching_if_none_match_returns_304_without_body(root):
    first, _ = await _get(root, "/static/data.txt")
    resp, body = await _get(root, "/static/data.txt", **{"if-none-match": first.headers["etag"]})
    assert resp.status == 304
    assert body == b""


async def test_weak_etag_still_matches(root):
    first, _ = await _get(root, "/static/data.txt")
    weak = "W/" + first.headers["etag"]
    resp, _ = await _get(root, "/static/data.txt", **{"if-none-match": weak})
    assert resp.status == 304


async def test_if_none_match_star_matches_any_file(root):
    resp, _ = await _get(root, "/static/data.txt", **{"if-none-match": "*"})
    assert resp.status == 304


async def test_if_modified_since_in_future_returns_304(root):
    future = formatdate(timeval=4102444800, usegmt=True)  # year 2100
    resp, _ = await _get(root, "/static/data.txt", **{"if-modified-since": future})
    assert resp.status == 304


async def test_bad_if_modified_since_is_ignored(root):
    resp, body = await _get(root, "/static/data.txt", **{"if-modified-since": "not a date"})
    assert resp.status == 200
    assert body == CONTENT


async def test_missing_file_is_404(root):
    resp, _ = await _get(root, "/static/nope.txt")
    assert resp.status == 404


async def test_path_traversal_is_blocked(root):
    resp, body = await _get(root, "/static/../secret.txt")
    assert resp.status == 404
    assert b"do not serve" not in body


async def test_encoded_traversal_is_blocked(root):
    resp, body = await _get(root, "/static/%2e%2e/secret.txt")
    assert resp.status == 404
    assert b"do not serve" not in body


async def test_other_paths_go_to_next_middleware(root):
    static = StaticFiles("/static", root)
    resp = await _call(static, _request("/api/thing"))
    assert resp.status == 404
    assert resp.body == b"from next"


async def test_post_under_prefix_is_405(root):
    static = StaticFiles("/static", root)
    resp = await _call(static, _request("/static/data.txt", method="POST"))
    assert resp.status == 405
    assert resp.headers["allow"] == "GET, HEAD"


async def test_oversized_file_is_413(root):
    static = StaticFiles("/static", root, max_file_size=5)
    resp = await _call(static, _request("/static/data.txt"))
    assert resp.status == 413


async def test_missing_directory_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        StaticFiles("/static", tmp_path / "nope")
