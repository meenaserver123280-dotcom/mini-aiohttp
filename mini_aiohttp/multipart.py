"""multipart/form-data: encoding for the client and parsing for the server."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

MAX_PARTS = 200
_UNSAFE_RE = re.compile(r'[\x00\r\n"]')
_BOUNDARY_RE = re.compile(r'boundary=(?:"([^"]+)"|([^;\s]+))')


@dataclass
class UploadedFile:
    filename: str
    content_type: str
    data: bytes


def _check(text: str, what: str) -> None:
    if _UNSAFE_RE.search(text):
        raise ValueError(f"Unsafe characters in {what}: {text!r}")


def _to_bytes(value, what: str) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    # bytes(5) would silently create five zero bytes, so reject anything else explicitly.
    raise TypeError(f"{what} must be str or bytes, not {type(value).__name__}")


def encode_multipart(fields=None, files=None):
    """Build a multipart/form-data body.

    fields: {name: str | bytes | list of those}
    files:  {name: (filename, data, content_type)}  where data is bytes or str
    Returns (body_bytes, content_type_header_value).
    """
    fields = fields or {}
    files = files or {}
    boundary = "----mini" + secrets.token_hex(16)
    chunks = []
    for name, value in fields.items():
        _check(name, "field name")
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            data = _to_bytes(item, f"field {name!r}")
            head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            chunks.append(head.encode("utf-8") + data + b"\r\n")
    for name, spec in files.items():
        _check(name, "field name")
        filename, data, *rest = spec
        content_type = rest[0] if rest else "application/octet-stream"
        _check(filename, "filename")
        _check(content_type, "content type")
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
        )
        chunks.append(head.encode("utf-8") + _to_bytes(data, f"file {name!r}") + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _param(disposition: str, key: str):
    match = re.search(r'(?:^|;)\s*' + key + r'="([^"]*)"', disposition)
    return match.group(1) if match else None


def parse_multipart(body: bytes, content_type: str, max_parts: int = MAX_PARTS) -> dict:
    """Parse a multipart/form-data body into {name: [str | UploadedFile, ...]}.

    Raises ValueError for malformed bodies, non-multipart content types, a missing
    boundary or closing delimiter, or too many parts.
    """
    content_type = content_type or ""
    match = _BOUNDARY_RE.search(content_type)
    if not content_type.lower().startswith("multipart/form-data") or not match:
        raise ValueError("Not a multipart/form-data body")
    boundary = (match.group(1) or match.group(2)).encode("latin-1")

    # Every delimiter is CRLF--boundary. The body is prefixed with CRLF so that a body
    # starting with the delimiter splits the same way as any other part.
    marker = b"\r\n--" + boundary
    segments = (b"\r\n" + body).split(marker)
    if len(segments) < 2:
        raise ValueError("Multipart boundary not found in body")
    if len(segments) - 2 > max_parts:
        raise ValueError("Too many multipart parts")

    result: dict = {}
    closed = False
    for segment in segments[1:]:
        if segment.startswith(b"--"):
            closed = True
            break  # closing delimiter; anything after it is epilogue and ignored
        if not segment.startswith(b"\r\n"):
            raise ValueError("Malformed multipart delimiter")
        # The part's content runs up to the next delimiter, which already consumed its CRLF.
        # Do NOT strip a trailing CRLF here: it can be real content of the uploaded file.
        head, sep, data = segment[2:].partition(b"\r\n\r\n")
        if not sep:
            raise ValueError("Malformed multipart part")

        headers = {}
        for line in head.split(b"\r\n"):
            key, _, val = line.decode("utf-8", "replace").partition(":")
            headers[key.strip().lower()] = val.strip()

        disposition = headers.get("content-disposition", "")
        name = _param(disposition, "name")
        if name is None:
            raise ValueError("Multipart part has no field name")
        filename = _param(disposition, "filename")
        if filename is not None:
            value = UploadedFile(
                filename, headers.get("content-type", "application/octet-stream"), data
            )
        else:
            try:
                value = data.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("Form field is not valid UTF-8")
        result.setdefault(name, []).append(value)

    if not closed:
        raise ValueError("Multipart body is missing its closing boundary")
    return result
