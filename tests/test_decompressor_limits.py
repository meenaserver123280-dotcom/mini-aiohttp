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


def test_zlib_decoder_handles_fragmented_input():
    body = b"fragmented input" * 100
    compressed = gzip.compress(body)
    decoder = ContentDecoder("gzip", max_output_size=len(body))
    output = b"".join(
        decoder.feed(compressed[index:index + 3])
        for index in range(0, len(compressed), 3)
    )
    output += decoder.finish()
    assert output == body


def test_raw_deflate_fallback_respects_output_limit():
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(b"x" * 100_000) + compressor.flush()
    decoder = ContentDecoder("deflate", max_output_size=100)
    with pytest.raises(ClientError, match="max_response_size"):
        decoder.feed(compressed)
