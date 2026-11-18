import shutil
import ssl
import subprocess

import pytest

from mini_aiohttp import HTTP2Client, HTTP2Server, HTTP3Client, HTTP3Server


def certificate(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("openssl is not installed")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], check=True, capture_output=True)
    return cert, key


async def test_http2_tls_alpn_roundtrip(tmp_path):
    pytest.importorskip("h2")
    cert, key = certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))

    async def handler(method, path, headers, body):
        return 200, {"content-type": "text/plain"}, method + ":" + path + ":" + body.decode()

    server = HTTP2Server(handler, ssl_context=context)
    await server.start("127.0.0.1", 0)
    port = server._server.sockets[0].getsockname()[1]
    client_context = ssl.create_default_context(cafile=str(cert))
    try:
        status, headers, body = await HTTP2Client(ssl_context=client_context).request(
            "POST", f"https://localhost:{port}/h2", data=b"ok"
        )
        assert status == 200
        assert headers["content-type"] == "text/plain"
        assert body == b"POST:/h2:ok"
    finally:
        await server.close()


async def test_http3_quic_roundtrip(tmp_path):
    pytest.importorskip("aioquic")
    cert, key = certificate(tmp_path)

    async def handler(method, path, headers, body):
        return 200, {"content-type": "text/plain"}, method + ":" + path + ":" + body.decode()

    server = HTTP3Server(handler, certificate=str(cert), private_key=str(key))
    await server.start("127.0.0.1", 0)
    # aioquic server exposes its bound UDP socket on the underlying transport.
    sock = server._server._transport.get_extra_info("sockname")
    port = sock[1]
    try:
        status, headers, body = await HTTP3Client(verify=False).request(
            "POST", f"https://127.0.0.1:{port}/h3", data=b"ok"
        )
        assert status == 200
        assert body == b"POST:/h3:ok"
    finally:
        await server.close()
