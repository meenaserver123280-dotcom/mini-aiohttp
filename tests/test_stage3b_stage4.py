import asyncio
import json
import shutil
import ssl
import subprocess

import pytest

from mini_aiohttp import ClientError, ClientSession, Response, Server
from mini_aiohttp.multipart import UploadedFile, encode_multipart, parse_multipart


# ---------- multipart (unit) ----------

def test_multipart_roundtrip_fields_and_files():
    body, ctype = encode_multipart(
        {"a": "1", "tags": ["x", "y"]},
        {"doc": ("note.txt", b"hello", "text/plain")},
    )
    parsed = parse_multipart(body, ctype)
    assert parsed["a"] == ["1"]
    assert parsed["tags"] == ["x", "y"]
    f = parsed["doc"][0]
    assert isinstance(f, UploadedFile)
    assert (f.filename, f.data, f.content_type) == ("note.txt", b"hello", "text/plain")


def test_multipart_rejects_unsafe_names():
    with pytest.raises(ValueError):
        encode_multipart({'a"b': "x"}, {})
    with pytest.raises(ValueError):
        encode_multipart({}, {"f": ('x"y', b"", "text/plain")})


def test_parse_rejects_non_multipart():
    with pytest.raises(ValueError):
        parse_multipart(b"x", "application/json")


def test_parse_limits_part_count():
    body, ctype = encode_multipart({f"k{i}": "v" for i in range(5)}, {})
    with pytest.raises(ValueError):
        parse_multipart(body, ctype, max_parts=3)


# ---------- fixtures ----------

@pytest.fixture
async def app():
    srv = Server()

    @srv.route("/form", method="POST")
    async def form(req):
        out = {}
        for name, values in req.form().items():
            out[name] = [
                v if isinstance(v, str) else
                {"filename": v.filename, "data": v.data.decode(), "type": v.content_type}
                for v in values
            ]
        return Response.json(out)

    @srv.route("/host")
    async def host(req):
        return Response(200, req.headers.get("host", ""))

    await srv.start("127.0.0.1", 0)
    yield srv
    await srv.close()


async def raw(port: int, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    return data


# ---------- multipart over HTTP ----------

async def test_multipart_upload_roundtrip(app):
    async with ClientSession() as s:
        r = await s.post(
            f"http://127.0.0.1:{app.port}/form",
            form={"name": "Asha"},
            files={"avatar": ("a.png", b"PNG", "image/png")},
        )
    body = json.loads(r.body)
    assert body["name"] == ["Asha"]
    assert body["avatar"][0]["filename"] == "a.png"
    assert body["avatar"][0]["data"] == "PNG"


async def test_json_and_form_together_rejected(app):
    async with ClientSession() as s:
        with pytest.raises(ValueError):
            await s.post(f"http://127.0.0.1:{app.port}/form", json={"a": 1}, form={"b": "2"})


async def test_malformed_multipart_gets_400_from_handler():
    srv = Server()

    @srv.route("/form", method="POST")
    async def form(req):
        try:
            req.form()
        except ValueError as exc:
            return Response(400, str(exc))
        return Response(200, "ok")

    await srv.start("127.0.0.1", 0)
    try:
        async with ClientSession() as s:
            r = await s.post(
                f"http://127.0.0.1:{srv.port}/form",
                data=b"junk",
                headers={"Content-Type": "multipart/form-data; boundary=zzz"},
            )
        assert r.status == 400
    finally:
        await srv.close()


# ---------- HTTP proxy ----------

async def test_http_proxy_gets_absolute_url_and_target_host(app):
    # The test server acts as the proxy. It receives the absolute URL and routes on the path.
    async with ClientSession(proxy=f"http://127.0.0.1:{app.port}") as s:
        r = await s.get("http://target.example/host")
    assert r.body == b"target.example"


async def test_https_through_http_proxy_uses_connect_tunnel(app):
    # The HTTP app is not a CONNECT proxy; verify the client attempts CONNECT
    # and reports the proxy's actual rejection instead of claiming the feature
    # is unsupported.
    async with ClientSession(proxy=f"http://127.0.0.1:{app.port}") as s:
        with pytest.raises(ClientError, match="CONNECT failed with status 400"):
            await s.get("https://target.example/")


def test_proxy_scheme_must_be_known():
    with pytest.raises(ValueError):
        ClientSession(proxy="ftp://127.0.0.1:21")


# ---------- DNS cache ----------

async def test_dns_cache_stores_resolved_address(app):
    async with ClientSession(dns_cache_ttl=60) as s:
        await s.get(f"http://127.0.0.1:{app.port}/host")
        assert "127.0.0.1" in s._dns


async def test_dns_cache_disabled_when_ttl_zero(app):
    async with ClientSession(dns_cache_ttl=0) as s:
        await s.get(f"http://127.0.0.1:{app.port}/host")
        assert s._dns == {}


# ---------- static files ----------

async def test_static_serves_file(tmp_path):
    (tmp_path / "hello.txt").write_text("hi there")
    srv = Server()
    srv.static("/static", tmp_path)
    await srv.start("127.0.0.1", 0)
    try:
        async with ClientSession() as s:
            r = await s.get(f"http://127.0.0.1:{srv.port}/static/hello.txt")
        assert r.status == 200
        assert r.body == b"hi there"
        assert r.headers["content-type"].startswith("text/plain")
    finally:
        await srv.close()


async def test_static_blocks_path_traversal(tmp_path):
    base = tmp_path / "public"
    base.mkdir()
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    srv = Server()
    srv.static("/static", base)
    await srv.start("127.0.0.1", 0)
    try:
        data = await raw(srv.port, b"GET /static/../secret.txt HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert data.startswith(b"HTTP/1.1 404")
        assert b"TOP SECRET" not in data
        encoded = await raw(srv.port, b"GET /static/%2e%2e/secret.txt HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert b"TOP SECRET" not in encoded
    finally:
        await srv.close()


async def test_static_rejects_post(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    srv = Server()
    srv.static("/static", tmp_path)
    await srv.start("127.0.0.1", 0)
    try:
        data = await raw(srv.port, b"POST /static/a.txt HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        assert data.startswith(b"HTTP/1.1 405")
    finally:
        await srv.close()


def test_static_requires_existing_directory(tmp_path):
    with pytest.raises(ValueError):
        Server().static("/static", tmp_path / "missing")


# ---------- HTTPS ----------

def make_self_signed(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not installed")
async def test_https_server_roundtrip(tmp_path):
    cert, key = make_self_signed(tmp_path)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(str(cert), str(key))

    srv = Server()

    @srv.route("/")
    async def index(req):
        return Response(200, "secure")

    await srv.start("127.0.0.1", 0, ssl=server_ctx)
    try:
        client_ctx = ssl.create_default_context(cafile=str(cert))
        async with ClientSession(ssl_context=client_ctx) as s:
            r = await s.get(f"https://127.0.0.1:{srv.port}/")
        assert r.status == 200
        assert r.body == b"secure"
    finally:
        await srv.close()


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not installed")
async def test_https_client_rejects_untrusted_certificate(tmp_path):
    cert, key = make_self_signed(tmp_path)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(str(cert), str(key))
    srv = Server()

    @srv.route("/")
    async def index(req):
        return Response(200, "secure")

    await srv.start("127.0.0.1", 0, ssl=server_ctx)
    try:
        async with ClientSession() as s:  # default context does not trust the self-signed cert
            with pytest.raises(ssl.SSLError):
                await s.get(f"https://127.0.0.1:{srv.port}/")
    finally:
        await srv.close()