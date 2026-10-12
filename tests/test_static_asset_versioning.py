import os
from urllib.parse import parse_qs, urlsplit

from flask import url_for

import app as app_module


def asset_url(filename, **kwargs):
    with app_module.app.test_request_context():
        return url_for("static", filename=filename, **kwargs)


def test_static_css_and_javascript_urls_use_stable_file_versions(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    css_path = static_dir / "style.css"
    js_path = static_dir / "app.js"
    css_path.write_text("body { color: black; }", encoding="utf-8")
    js_path.write_text("console.log('test');", encoding="utf-8")
    monkeypatch.setattr(app_module.app, "static_folder", str(static_dir))

    first_css_url = asset_url("style.css", v="manual-version")
    first_js_url = asset_url("app.js")

    assert parse_qs(urlsplit(first_css_url).query)["v"] == [
        f"{css_path.stat().st_mtime_ns:x}-{css_path.stat().st_size:x}"
    ]
    assert parse_qs(urlsplit(first_js_url).query)["v"] == [
        f"{js_path.stat().st_mtime_ns:x}-{js_path.stat().st_size:x}"
    ]
    assert asset_url("style.css") == first_css_url
    first_js_version = parse_qs(urlsplit(first_js_url).query)["v"]

    css_stat = css_path.stat()
    css_path.write_text("body { color: green; }", encoding="utf-8")
    os.utime(
        css_path,
        ns=(css_stat.st_atime_ns, css_stat.st_mtime_ns + 2_000_000_000),
    )
    changed_css_url = asset_url("style.css")
    assert changed_css_url != first_css_url
    assert parse_qs(urlsplit(asset_url("app.js")).query)["v"] == first_js_version

    js_stat = js_path.stat()
    js_path.write_text("console.log('changed');", encoding="utf-8")
    os.utime(
        js_path,
        ns=(js_stat.st_atime_ns, js_stat.st_mtime_ns + 2_000_000_000),
    )
    assert parse_qs(urlsplit(asset_url("app.js")).query)["v"] != first_js_version
    assert asset_url("style.css") == changed_css_url


def test_static_non_css_javascript_urls_are_not_versioned():
    assert "?" not in asset_url("favicon.svg")


def test_unversioned_static_requests_redirect_to_the_versioned_asset(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    stylesheet = static_dir / "style.css"
    stylesheet.write_text("body { color: black; }", encoding="utf-8")
    monkeypatch.setattr(app_module.app, "static_folder", str(static_dir))

    response = app_module.app.test_client().get("/static/style.css")

    assert response.status_code == 302
    assert response.headers["Location"] == asset_url("style.css")
    assert response.headers["Cache-Control"] == "no-store"


def test_static_asset_versioning_does_not_change_static_route():
    url = asset_url("drawings.css")

    assert url.startswith("/static/drawings.css?")
    assert app_module.app.url_map.bind("localhost").match(urlsplit(url).path)[0] == "static"
