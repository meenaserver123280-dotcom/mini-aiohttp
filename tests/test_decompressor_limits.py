import gzip
import zlib

import pytest

from mini_aiohttp import ClientError
from mini_aiohttp._encoding import ContentDecoder


@pytest.mark.parametrize(
    ("encoding", "compressed"),
    [
        ("gzip", gzip.compress(b"x" * (4 * 1024 * 1024))),
        ("deflate", zlib.compress(b"x" * (4 * 1024 * 1024))),
    ],
)
def test_zlib_decoder_enforces_output_cap_during_decompression(encoding, compressed):
    decoder = ContentDecoder(encoding, max_output_size=1024)

    with pytest.raises(ClientError, match="max_response_size"):
        decoder.feed(compressed)

    # The decoder never records more than the configured output budget.
    assert decoder._steps[0]._output_size <= 1024


def test_zlib_decoder_allows_output_at_exact_limit():
    body = b"bounded response"
    decoder = ContentDecoder("gzip", max_output_size=len(body))

    output = decoder.feed(gzip.compress(body)) + decoder.finish()

    assert output == body


def test_zlib_decoder_rejects_output_one_byte_over_limit():
    body = b"bounded response"
    decoder = ContentDecoder("gzip", max_output_size=len(body) - 1)

    with pytest.raises(ClientError, match="max_response_size"):
        decoder.feed(gzip.compress(body))


def test_zlib_decoder_none_disables_output_limit():
    body = b"x" * (2 * 1024 * 1024)
    decoder = ContentDecoder("gzip", max_output_size=None)

    output = decoder.feed(gzip.compress(body)) + decoder.finish()

    assert output == body
