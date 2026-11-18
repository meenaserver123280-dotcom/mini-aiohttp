"""HTTPS through an HTTP CONNECT proxy and through a SOCKS5 proxy.

Each test starts a local TLS origin, a local proxy, and a client that trusts a
self-signed certificate generated at test time. Needs the 'cryptography' package;
the tests are skipped when it is missing.
"""
import asyncio
import datetime
import ipaddress
import ssl
import struct

import pytest

from mini_aiohttp import ClientSession


def _make_cert(directory):
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture
def tls(tmp_path):
    cert_path, key_path = _make_cert(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_path, key_path)
    client_ctx = ssl.create_default_context(cafile=str(cert_path))
    return server_ctx, client_ctx


async def _relay(c_reader, c_writer, t_reader, t_writer):
    """Copy bytes both ways until either side closes."""
    async def pipe(src, dst):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            dst.close()

    await asyncio.gather(pipe(c_reader, t_writer), pipe(t_reader, c_writer))


async def _start_tls_origin(server_ctx):
    async def handler(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
            await writer.drain()
        finally:
            writer.close()

    return await asyncio.start_server(handler, "127.0.0.1", 0, ssl=server_ctx)


async def _start_connect_proxy():
    async def handler(c_reader, c_writer):
        head = await c_reader.readuntil(b"\r\n\r\n")
        method, authority, _ = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
        assert method == "CONNECT"
        host, _, port = authority.rpartition(":")
        t_reader, t_writer = await asyncio.open_connection(host, int(port))
        c_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await c_writer.drain()
        await _relay(c_reader, c_writer, t_reader, t_writer)

    return await asyncio.start_server(handler, "127.0.0.1", 0)


async def _start_socks5_proxy():
    async def handler(c_reader, c_writer):
        # Greeting: VER, NMETHODS, METHODS. Answer: no authentication required.
        _ver, nmethods = await c_reader.readexactly(2)
        await c_reader.readexactly(nmethods)
        c_writer.write(b"\x05\x00")
        await c_writer.drain()
        # Request: VER, CMD, RSV, ATYP. Only IPv4 targets are used by these tests.
        _ver, cmd, _rsv, atyp = await c_reader.readexactly(4)
        assert cmd == 1 and atyp == 1
        host = str(ipaddress.IPv4Address(await c_reader.readexactly(4)))
        (port,) = struct.unpack("!H", await c_reader.readexactly(2))
        t_reader, t_writer = await asyncio.open_connection(host, port)
        c_writer.write(b"\x05\x00\x00\x01" + bytes(4) + b"\x00\x00")
        await c_writer.drain()
        await _relay(c_reader, c_writer, t_reader, t_writer)

    return await asyncio.start_server(handler, "127.0.0.1", 0)


async def test_https_through_http_connect_proxy(tls):
    server_ctx, client_ctx = tls
    origin = await _start_tls_origin(server_ctx)
    proxy = await _start_connect_proxy()
    o_port = origin.sockets[0].getsockname()[1]
    p_port = proxy.sockets[0].getsockname()[1]
    async with origin, proxy:
        async with ClientSession(proxy=f"http://127.0.0.1:{p_port}", ssl_context=client_ctx) as s:
            r = await s.get(f"https://127.0.0.1:{o_port}/")
    assert r.status == 200
    assert r.body == b"hello"


async def test_https_through_socks5_proxy(tls):
    server_ctx, client_ctx = tls
    origin = await _start_tls_origin(server_ctx)
    proxy = await _start_socks5_proxy()
    o_port = origin.sockets[0].getsockname()[1]
    p_port = proxy.sockets[0].getsockname()[1]
    async with origin, proxy:
        async with ClientSession(proxy=f"socks5://127.0.0.1:{p_port}", ssl_context=client_ctx) as s:
            r = await s.get(f"https://127.0.0.1:{o_port}/")
    assert r.status == 200
    assert r.body == b"hello"
