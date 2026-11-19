"""Template engine: escaping, lookups, loader caching and path safety."""
from __future__ import annotations

import os

import pytest

from mini_aiohttp.templates import Template, TemplateError, TemplateLoader, html_response


def test_values_are_escaped_by_default():
    out = Template("<p>{{ text }}</p>").render(text='<script>alert("x")</script> & \'q\'')
    assert out == "<p>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &#x27;q&#x27;</p>"


def test_raw_marker_skips_escaping():
    assert Template("{{! markup }}").render(markup="<b>ok</b>") == "<b>ok</b>"


def test_dotted_lookup_in_dicts_and_objects():
    class User:
        name = "Ada"

    out = Template("{{ user.name }} / {{ acct.owner.name }}").render(
        user={"name": "Grace"}, acct=type("A", (), {"owner": User()})()
    )
    assert out == "Grace / Ada"


def test_none_renders_empty_and_numbers_render_as_text():
    assert Template("[{{ a }}][{{ b }}]").render(a=None, b=3.5) == "[][3.5]"


def test_missing_variable_raises():
    with pytest.raises(TemplateError):
        Template("{{ missing }}").render()


def test_missing_nested_key_raises():
    with pytest.raises(TemplateError):
        Template("{{ user.email }}").render(user={"name": "Ada"})


def test_unmatched_braces_stay_literal():
    assert Template("{{ 1 + 2 }} and {single}").render() == "{{ 1 + 2 }} and {single}"


def test_whitespace_inside_tags_is_allowed():
    assert Template("{{title}}|{{   title   }}").render(title="x") == "x|x"


def test_loader_renders_and_caches(tmp_path):
    (tmp_path / "page.html").write_text("<h1>{{ title }}</h1>", encoding="utf-8")
    loader = TemplateLoader(tmp_path)
    first = loader.get("page.html")
    assert loader.get("page.html") is first
    assert loader.render("page.html", title="Hi") == "<h1>Hi</h1>"


def test_loader_reloads_when_file_changes(tmp_path):
    path = tmp_path / "page.html"
    path.write_text("v1 {{ x }}", encoding="utf-8")
    loader = TemplateLoader(tmp_path)
    assert loader.render("page.html", x=1) == "v1 1"
    path.write_text("v2 {{ x }}", encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
    assert loader.render("page.html", x=1) == "v2 1"


def test_loader_blocks_path_traversal(tmp_path):
    root = tmp_path / "templates"
    root.mkdir()
    (tmp_path / "secret.html").write_text("secret", encoding="utf-8")
    loader = TemplateLoader(root)
    with pytest.raises(TemplateError):
        loader.get("../secret.html")


def test_loader_unknown_template_raises(tmp_path):
    with pytest.raises(TemplateError):
        TemplateLoader(tmp_path).get("nope.html")


def test_loader_rejects_missing_directory(tmp_path):
    with pytest.raises(ValueError):
        TemplateLoader(tmp_path / "missing")


def test_html_response_sets_content_type():
    resp = html_response("<p>hi</p>", status=201)
    assert resp.status == 201
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert resp.body == b"<p>hi</p>"
