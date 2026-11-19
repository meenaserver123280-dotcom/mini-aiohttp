"""urlencoded form parsing helper."""
from __future__ import annotations

import pytest

from mini_aiohttp import Request
from mini_aiohttp.forms import FormError, parse_urlencoded

FORM = {"content-type": "application/x-www-form-urlencoded"}


def _req(body: bytes, headers=None):
    return Request("POST", "/form", "", "HTTP/1.1", headers if headers is not None else dict(FORM), body)


def test_parses_fields_and_decodes_plus_and_percent():
    fields = parse_urlencoded(_req(b"name=Ada+Lovelace&city=L%C3%B3ndon"))
    assert fields == {"name": ["Ada Lovelace"], "city": ["Lóndon"]}


def test_repeated_names_keep_all_values():
    assert parse_urlencoded(_req(b"tag=a&tag=b"))["tag"] == ["a", "b"]


def test_blank_values_are_kept():
    assert parse_urlencoded(_req(b"empty=&other=1")) == {"empty": [""], "other": ["1"]}


def test_content_type_parameters_are_allowed():
    headers = {"content-type": "Application/X-WWW-Form-Urlencoded; charset=utf-8"}
    assert parse_urlencoded(_req(b"a=1", headers)) == {"a": ["1"]}


def test_wrong_content_type_is_415():
    with pytest.raises(FormError) as info:
        parse_urlencoded(_req(b"a=1", {"content-type": "application/json"}))
    assert info.value.status == 415


def test_missing_content_type_is_415():
    with pytest.raises(FormError) as info:
        parse_urlencoded(_req(b"a=1", {}))
    assert info.value.status == 415


def test_body_over_limit_is_413():
    with pytest.raises(FormError) as info:
        parse_urlencoded(_req(b"a=" + b"x" * 100), max_bytes=50)
    assert info.value.status == 413


def test_too_many_fields_is_400():
    body = b"&".join(f"k{i}=v".encode() for i in range(5))
    with pytest.raises(FormError) as info:
        parse_urlencoded(_req(body), max_fields=3)
    assert info.value.status == 400


def test_non_ascii_raw_bytes_are_400():
    with pytest.raises(FormError) as info:
        parse_urlencoded(_req("name=Ó".encode("utf-8")))
    assert info.value.status == 400


def test_empty_body_gives_no_fields():
    assert parse_urlencoded(_req(b"")) == {}
