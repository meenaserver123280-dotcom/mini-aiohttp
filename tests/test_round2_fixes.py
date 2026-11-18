import pytest

from mini_aiohttp.batch import _check_concurrency
from mini_aiohttp.multipart import encode_multipart, parse_multipart


def test_multipart_roundtrip_keeps_trailing_crlf_in_file_data():
    payload = b"line one\r\nline two\r\n"  # file content that ends with CRLF
    body, ctype = encode_multipart(
        fields={"title": "hello"},
        files={"doc": ("a.txt", payload, "text/plain")},
    )
    parsed = parse_multipart(body, ctype)
    assert parsed["title"] == ["hello"]
    assert parsed["doc"][0].data == payload


def test_multipart_roundtrip_with_str_file_content():
    body, ctype = encode_multipart(files={"doc": ("a.txt", "héllo", "text/plain")})
    parsed = parse_multipart(body, ctype)
    assert parsed["doc"][0].data == "héllo".encode("utf-8")


def test_multipart_encoder_rejects_integers_instead_of_zero_bytes():
    with pytest.raises(TypeError):
        encode_multipart(fields={"n": 5})


@pytest.mark.parametrize("bad", [0, -1])
def test_batch_rejects_non_positive_concurrency(bad):
    with pytest.raises(ValueError):
        _check_concurrency(bad)


def test_batch_accepts_positive_concurrency():
    _check_concurrency(1)
    _check_concurrency(100)
