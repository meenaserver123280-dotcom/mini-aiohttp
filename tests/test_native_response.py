import pytest

from mini_aiohttp.response import Response

try:
    from mini_aiohttp._native import serialize_response
except ImportError:
    serialize_response = None


@pytest.mark.skipif(serialize_response is None, reason="optional Rust extension is not built")
@pytest.mark.parametrize("keep_alive", [True, False])
def test_native_response_serializer_matches_python(keep_alive):
    response = Response(201, 'hello', {'content-type': 'text/plain', 'x-powered-by': 'mini-aiohttp'})
    response.add_set_cookie('session=abc; HttpOnly')
    headers = dict(response.headers)
    headers.setdefault('content-type', 'text/plain; charset=utf-8')
    headers['content-length'] = str(len(response.body))
    headers['connection'] = 'keep-alive' if keep_alive else 'close'
    expected = (
        '\\r\\n'.join(
            [f'HTTP/1.1 {response.status} {response.reason}']
            + [f'{key.title()}: {value}' for key, value in headers.items()]
            + [f'Set-Cookie: {cookie}' for cookie in response._out_set_cookies]
        )
        + '\\r\\n\\r\\n'
    ).encode('latin-1') + response.body
    actual = serialize_response(
        response.status, response.reason, headers, response._out_set_cookies, response.body, keep_alive
    )
    assert actual == expected
@pytest.mark.skipif(serialize_response is None, reason="optional Rust extension is not built")
def test_native_response_serializer_stringifies_mutated_header_values():
    response = Response(200, b"ok")
    response.headers["x-count"] = 7
    response.add_set_cookie(123)
    data = response.to_bytes()
    assert b"X-Count: 7\\r\\n" in data
    assert b"Set-Cookie: 123\\r\\n" in data
