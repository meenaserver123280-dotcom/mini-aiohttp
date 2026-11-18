import pytest

from mini_aiohttp import ClientSession, CookieJar


def test_build_head_never_duplicates_header_in_any_case():
    s = ClientSession()
    head = s._build_head(
        "POST", "/", "example.com",
        {"user-agent": "custom", "content-type": "text/plain"},
        content_length=3, chunked=False,
    ).decode("latin-1")
    lines = head.split("\r\n")
    names = [line.split(":", 1)[0].lower() for line in lines[1:] if ":" in line]
    assert names.count("user-agent") == 1
    assert "user-agent: custom" in lines
    assert names.count("content-length") == 1


async def test_caller_cannot_set_framing_headers():
    s = ClientSession()
    with pytest.raises(ValueError, match="cannot be passed"):
        await s.request("POST", "http://127.0.0.1:1/", headers={"Content-Length": "999"}, data=b"x")
    await s.close()


def test_cookie_with_max_age_expires():
    now = [1000.0]
    jar = CookieJar(clock=lambda: now[0])
    jar.update("https://example.com/", ["sid=abc; Max-Age=60; Path=/"])
    assert jar.header_for("https://example.com/") == "sid=abc"
    now[0] += 61
    assert jar.header_for("https://example.com/") is None
