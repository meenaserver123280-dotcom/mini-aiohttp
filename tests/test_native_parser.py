import pytest

try:
    from mini_aiohttp._native import parse_request_head
except ImportError:
    parse_request_head = None


@pytest.mark.skipif(parse_request_head is None, reason="optional Rust extension is not built")
def test_native_parser_decodes_request_head():
    parsed = parse_request_head(
        b"GET /hello?q=1 HTTP/1.1\r\nHost: example.test\r\nX-Test:  value \t\r\n\r\n"
    )
    assert parsed == (
        "GET",
        "/hello?q=1",
        "HTTP/1.1",
        [("Host", "example.test"), ("X-Test", "value")],
    )


@pytest.mark.skipif(parse_request_head is None, reason="optional Rust extension is not built")
def test_native_parser_rejects_obsolete_header_folding():
    with pytest.raises(ValueError, match="Obsolete line folding"):
        parse_request_head(b"GET / HTTP/1.1\r\n folded: value\r\n\r\n")