"""Minimal SOCKS5 CONNECT client (RFC 1928, username/password auth per RFC 1929).

Uses only the standard library. Domain names are sent to the proxy, so DNS can be
resolved there (socks5h://). IP literals are sent as IPv4 or IPv6 addresses.
"""
from __future__ import annotations

import asyncio
import ipaddress

from .exceptions import ClientError


async def _read(reader, size: int) -> bytes:
    try:
        return await reader.readexactly(size)
    except asyncio.IncompleteReadError as exc:
        raise ClientError("SOCKS5 proxy closed the connection") from exc


def _address_field(target_host: str) -> bytes:
    """Encode the destination as a SOCKS5 address (ATYP + ADDR), not just a domain name."""
    try:
        ip = ipaddress.ip_address(target_host.strip("[]"))
    except ValueError:
        try:
            host = target_host.encode("idna")
        except UnicodeError:
            raise ClientError("Invalid target host name for SOCKS5")
        if not 0 < len(host) <= 255:
            raise ClientError("Invalid target host name length for SOCKS5")
        return b"\x03" + bytes([len(host)]) + host
    if ip.version == 4:
        return b"\x01" + ip.packed
    return b"\x04" + ip.packed


async def open_socks5(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    username: str | None = None,
    password: str | None = None,
):
    """Open a TCP tunnel through a SOCKS5 proxy. Returns (reader, writer)."""
    reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
    try:
        offered = b"\x00" if username is None else b"\x00\x02"
        writer.write(b"\x05" + bytes([len(offered)]) + offered)
        await writer.drain()
        version, method = await _read(reader, 2)
        if version != 5:
            raise ClientError("Not a SOCKS5 proxy")

        if method == 0x02:
            if username is None:
                raise ClientError("SOCKS5 proxy requires authentication")
            user = username.encode("utf-8")
            pwd = (password or "").encode("utf-8")
            if len(user) > 255 or len(pwd) > 255:
                raise ClientError("SOCKS5 credentials are too long")
            writer.write(b"\x01" + bytes([len(user)]) + user + bytes([len(pwd)]) + pwd)
            await writer.drain()
            _, status = await _read(reader, 2)
            if status != 0:
                raise ClientError("SOCKS5 authentication failed")
        elif method != 0x00:
            raise ClientError("SOCKS5 proxy accepted none of the offered auth methods")

        address = _address_field(target_host)
        writer.write(b"\x05\x01\x00" + address + target_port.to_bytes(2, "big"))
        await writer.drain()

        version, reply, _, atyp = await _read(reader, 4)
        if version != 5 or reply != 0:
            raise ClientError(f"SOCKS5 connect failed (reply code {reply})")
        if atyp == 1:
            await _read(reader, 4 + 2)
        elif atyp == 4:
            await _read(reader, 16 + 2)
        elif atyp == 3:
            length = (await _read(reader, 1))[0]
            await _read(reader, length + 2)
        else:
            raise ClientError("SOCKS5 proxy sent an unknown address type")
        return reader, writer
    except BaseException:
        writer.close()
        raise
