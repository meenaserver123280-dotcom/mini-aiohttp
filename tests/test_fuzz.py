"""Property-based fuzzing of the parsers. Requires the 'test' extra (hypothesis).

These tests check that untrusted input can only produce the documented exception
types (BadRequest, ValueError), never IndexError, KeyError, TypeError or a hang.
"""
import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings, strategies as st  # noqa: E402

from mini_aiohttp.cookies import CookieJar  # noqa: E402
from mini_aiohttp.multipart import parse_multipart  # noqa: E402
from mini_aiohttp.server import BadRequest, _match_pattern, _parse_target, _split_path  # noqa: E402


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=200))
def test_request_target_parser_only_raises_bad_request(target):
    try:
        _parse_target(target)
    except BadRequest:
        pass


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=120), st.text(max_size=120))
def test_route_matching_never_crashes(pattern, path):
    _match_pattern(_split_path(pattern), _split_path(path))


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=300))
def test_multipart_parser_only_raises_value_error(body):
    try:
        parse_multipart(body, 'multipart/form-data; boundary="XX"')
    except ValueError:
        pass


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=200))
def test_cookie_parser_never_raises(raw):
    CookieJar().update("https://example.com/", [raw])
