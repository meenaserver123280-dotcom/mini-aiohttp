"""A small template engine with auto-escaping.

Syntax (deliberately minimal, no loops or conditions):

    {{ name }}          value, HTML-escaped
    {{! html }}         value, inserted raw. Only use it for trusted markup.
    {{ user.name }}     dotted lookup in dicts or attributes

Anything that does not match these forms is kept as literal text. A variable that is
missing from the context raises TemplateError, so typos fail loudly.

    loader = TemplateLoader("templates")

    @app.route("/")
    async def home(req):
        return html_response(loader.render("home.html", title="Hi", items=["a", "b"]))
"""
from __future__ import annotations

import html
import re
from collections.abc import Mapping
from pathlib import Path

from .response import Response

_TOKEN = re.compile(r"\{\{(!?)\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}")


class TemplateError(Exception):
    """A template could not be found, or a variable it uses is not in the context."""


def _lookup(context: Mapping, path: tuple):
    current = context
    for name in path:
        try:
            current = current[name] if isinstance(current, Mapping) else getattr(current, name)
        except (KeyError, AttributeError) as exc:
            raise TemplateError(f"Unknown template variable: {'.'.join(path)}") from exc
    return current


class Template:
    """A compiled template. Build it once and call render() many times."""

    def __init__(self, source: str) -> None:
        self._parts: list = []
        position = 0
        for match in _TOKEN.finditer(source):
            if match.start() > position:
                self._parts.append(source[position:match.start()])
            self._parts.append((match.group(1) == "!", tuple(match.group(2).split("."))))
            position = match.end()
        if position < len(source):
            self._parts.append(source[position:])

    def render(self, **context) -> str:
        out: list = []
        for part in self._parts:
            if isinstance(part, str):
                out.append(part)
                continue
            raw, path = part
            value = _lookup(context, path)
            text = "" if value is None else str(value)
            out.append(text if raw else html.escape(text, quote=True))
        return "".join(out)


class TemplateLoader:
    """Load templates from a directory. Files are cached and reloaded when they change."""

    def __init__(self, directory) -> None:
        self.base = Path(directory).resolve()
        if not self.base.is_dir():
            raise ValueError(f"Template directory does not exist: {directory}")
        self._cache: dict = {}

    def get(self, name: str) -> Template:
        try:
            target = (self.base / name).resolve()
            target.relative_to(self.base)
        except (ValueError, OSError):
            raise TemplateError(f"Template not found: {name}") from None
        if not target.is_file():
            raise TemplateError(f"Template not found: {name}")
        mtime = target.stat().st_mtime_ns
        cached = self._cache.get(target)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        template = Template(target.read_text(encoding="utf-8"))
        self._cache[target] = (mtime, template)
        return template

    def render(self, name: str, **context) -> str:
        return self.get(name).render(**context)


def html_response(body: str, status: int = 200) -> Response:
    """A UTF-8 HTML response."""
    return Response(status, body, {"Content-Type": "text/html; charset=utf-8"})


__all__ = ["Template", "TemplateError", "TemplateLoader", "html_response"]
