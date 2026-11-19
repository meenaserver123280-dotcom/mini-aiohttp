"""Helpers for application/x-www-form-urlencoded request bodies (plain HTML forms).

    from mini_aiohttp.forms import parse_urlencoded, FormError

    @app.route("/contact", method="POST")
    async def contact(req):
        try:
            fields = parse_urlencoded(req)
        except FormError as exc:
            return Response(exc.status, str(exc))
        name = fields.get("name", [""])[0]
"""
from __future__ import annotations

from urllib.parse import parse_qs

from .response import Request

DEFAULT_MAX_BYTES = 64 * 1024
DEFAULT_MAX_FIELDS = 100
_MEDIA_TYPE = "application/x-www-form-urlencoded"


class FormError(ValueError):
    """Raised for a body that cannot be used as a form. `status` is the HTTP status to return."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def parse_urlencoded(
    req: Request,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_fields: int = DEFAULT_MAX_FIELDS,
) -> dict[str, list[str]]:
    """Return the form fields as {name: [values]}. Blank values are kept.

    Raises FormError: 415 for the wrong Content-Type, 413 for a body over `max_bytes`,
    400 for too many fields or invalid UTF-8.
    """
    content_type = req.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != _MEDIA_TYPE:
        raise FormError(415, f"Expected Content-Type {_MEDIA_TYPE}")
    if len(req.body) > max_bytes:
        raise FormError(413, "Form body too large")
    try:
        return parse_qs(
            req.body.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=False,
            errors="strict",
            max_num_fields=max_fields,
        )
    except UnicodeDecodeError:
        raise FormError(400, "Form body is not valid form encoding") from None
    except ValueError as exc:
        raise FormError(400, f"Invalid form body: {exc}") from None
