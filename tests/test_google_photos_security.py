from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace

import pytest
from flask import Response

import app as app_module
from app import app


def public_dns(_hostname, _port, type=None):
    return [(None, None, None, None, ("142.250.72.14", 443))]


@pytest.fixture(autouse=True)
def public_google_dns(monkeypatch):
    monkeypatch.setattr(app_module.socket, "getaddrinfo", public_dns)


@pytest.mark.parametrize(
    "url",
    [
        "http://photos.google.com/share/token",
        "https://127.0.0.1/share/token",
        "https://photos.google.com@127.0.0.1/share/token",
        "https://photos.google.com:8443/share/token",
        "https://example.com/share/token",
        "https://photos.google.com/share/token#fragment",
    ],
)
def test_google_photos_share_url_validation_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        app_module._validate_google_photos_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://lh3.googleusercontent.com/pw/abc",
        "https://lh3.googleusercontent.com.evil.example/pw/abc",
        "https://lh3.googleusercontent.com/other/abc",
        "https://127.0.0.1/pw/abc",
    ],
)
def test_google_photos_image_validation_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        app_module._validate_google_photos_url(url, image=True)


def test_google_photos_url_validation_rejects_non_public_dns(monkeypatch):
    monkeypatch.setattr(
        app_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("10.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="public address"):
        app_module._validate_google_photos_url("https://photos.google.com/share/token")


def test_google_photos_redirect_target_is_validated_before_fetch(monkeypatch):
    calls = []

    class RedirectResponse:
        is_redirect = True
        is_permanent_redirect = False
        headers = {"Location": "http://127.0.0.1/private"}

        def close(self):
            pass

    def fake_get(url, **_kwargs):
        calls.append(url)
        return RedirectResponse()

    monkeypatch.setattr(app_module.requests, "get", fake_get)
    with pytest.raises(ValueError):
        app_module._fetch_google_photos_page("https://photos.google.com/share/token")
    assert calls == ["https://photos.google.com/share/token"]


def test_google_image_fetch_rejects_non_image_content_type(monkeypatch):
    class Response:
        is_redirect = False
        is_permanent_redirect = False
        headers = {"Content-Type": "text/html"}

        def raise_for_status(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(app_module.requests, "get", lambda *_args, **_kwargs: Response())
    with pytest.raises(ValueError, match="unsupported image type"):
        app_module._fetch_google_image("https://lh3.googleusercontent.com/pw/abc")


def test_google_image_fetch_enforces_streamed_size_limit(monkeypatch):
    class Response:
        is_redirect = False
        is_permanent_redirect = False
        headers = {"Content-Type": "image/jpeg"}

        def raise_for_status(self):
            pass

        def iter_content(self, _size):
            yield b"x" * (app_module.GOOGLE_IMAGE_MAX_BYTES + 1)

        def close(self):
            pass

    monkeypatch.setattr(app_module.requests, "get", lambda *_args, **_kwargs: Response())
    with pytest.raises(ValueError, match="too large"):
        app_module._fetch_google_image("https://lh3.googleusercontent.com/pw/abc")


def test_image_route_requires_image_permission(monkeypatch):
    monkeypatch.setattr(app_module, "get_current_user", lambda: {"id": "user"})
    monkeypatch.setattr(app_module, "_user_has_compiled_permission", lambda *_args: False)
    with app.test_request_context("/image?url=https%3A%2F%2Flh3.googleusercontent.com%2Fpw%2Fabc"):
        response = app.view_functions["image"]()
    assert isinstance(response, Response)
    assert response.status_code == 403


def test_google_photos_extraction_returns_validated_html_image_url_without_logging_urls(
    monkeypatch, caplog
):
    share_url = "https://photos.google.com/share/synthetic-share-id"
    image_url = "https://lh3.googleusercontent.com/pw/synthetic-image-id"
    monkeypatch.setattr(
        app_module,
        "_fetch_google_photos_page",
        lambda _url: f'<meta content="{image_url}">',
    )

    with caplog.at_level("INFO", logger=app.logger.name):
        result = app_module._extract_google_photos_image(share_url)

    assert result == f"{image_url}=w1200"
    assert "stage=html_parse outcome=image_found" in caplog.text
    assert "stage=complete outcome=success" in caplog.text
    assert share_url not in caplog.text
    assert image_url not in caplog.text


def test_google_photos_extraction_logs_timeout_and_browser_failure_safely(
    monkeypatch, caplog
):
    share_url = "https://photos.google.com/share/synthetic-share-id"
    monkeypatch.setattr(
        app_module,
        "_fetch_google_photos_page",
        lambda _url: (_ for _ in ()).throw(app_module.requests.Timeout()),
    )
    monkeypatch.setattr(
        app_module.browser_pool,
        "get_browser",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic browser startup failure")),
    )

    with caplog.at_level("WARNING", logger=app.logger.name):
        result = app_module._extract_google_photos_image(share_url)

    assert result is None
    assert "stage=html_fetch outcome=error" in caplog.text
    assert "error_type=Timeout" in caplog.text
    assert "stage=browser_acquire outcome=error" in caplog.text
    assert share_url not in caplog.text


def test_google_photos_browser_capture_returns_only_validated_image(monkeypatch, caplog):
    share_url = "https://photos.google.com/share/synthetic-share-id"
    image_url = "https://lh3.googleusercontent.com/pw/synthetic-image-id"
    monkeypatch.setattr(
        app_module,
        "_fetch_google_photos_page",
        lambda _url: "<html>no image link</html>",
    )

    class FakeResponse:
        url = image_url + "=w1200"

    class FakePage:
        def route(self, *_args, **_kwargs):
            pass

        def on(self, event, callback):
            assert event == "response"
            self.response_handler = callback

        def goto(self, *_args, **_kwargs):
            self.response_handler(FakeResponse())
            return type("NavigationResponse", (), {"status": 200})()

        def wait_for_timeout(self, _milliseconds):
            pass

        def close(self):
            pass

    class FakeBrowser:
        def new_page(self):
            return FakePage()

    monkeypatch.setattr(app_module.browser_pool, "get_browser", FakeBrowser)
    monkeypatch.setattr(app_module.browser_pool, "return_browser", lambda _browser: None)

    with caplog.at_level("INFO", logger=app.logger.name):
        result = app_module._extract_google_photos_image(share_url)

    assert result == f"{image_url}=w1200"
    assert "stage=browser_navigation outcome=complete" in caplog.text
    assert "status=200" in caplog.text
    assert "stage=browser_capture outcome=success" in caplog.text
    assert share_url not in caplog.text
    assert image_url not in caplog.text


def test_google_photos_extraction_endpoint_keeps_502_on_no_image(monkeypatch, caplog):
    share_url = "https://photos.google.com/share/synthetic-share-id"
    monkeypatch.setattr(app_module, "get_current_user", lambda: {"id": "user"})
    monkeypatch.setattr(app_module, "_google_photos_permission_denial", lambda *_args: None)
    monkeypatch.setattr(app_module, "get_cached_url", lambda _url: None)
    monkeypatch.setattr(app_module, "_extract_google_photos_image", lambda _url: None)

    with app.test_request_context(f"/extract-google-photos-url?url={share_url}"):
        with caplog.at_level("WARNING", logger=app.logger.name):
            response = app.view_functions["extract_google_photos_url"]()

    assert response.status_code == 502
    assert response.get_json() == {
        "error": "No Google Photos image preview was found in the shared page."
    }
    assert "extraction request status=502" in caplog.text
    assert share_url not in caplog.text


def test_google_photos_extraction_endpoint_returns_structured_preview_url(monkeypatch):
    share_url = "https://photos.google.com/share/structured-response-test"
    image_url = "https://lh3.googleusercontent.com/pw/structured-image=w1200"
    extraction_options = []
    monkeypatch.setattr(app_module, "get_current_user", lambda: {"id": "user"})
    monkeypatch.setattr(app_module, "_google_photos_permission_denial", lambda *_args: None)
    monkeypatch.setattr(
        app_module,
        "get_or_extract_google_photos_url",
        lambda _url, **kwargs: (extraction_options.append(kwargs), (image_url, "miss"))[1],
    )

    with app.test_request_context(f"/extract-google-photos-url?url={share_url}&retry=1"):
        response = app.view_functions["extract_google_photos_url"]()

    assert response.status_code == 200
    assert response.get_json() == {"url": image_url}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert extraction_options == [{"force_refresh": True}]


def test_google_photos_extraction_endpoint_returns_retryable_wait_timeout(monkeypatch):
    share_url = "https://photos.google.com/share/inflight-endpoint-timeout-test"
    monkeypatch.setattr(app_module, "get_current_user", lambda: {"id": "user"})
    monkeypatch.setattr(app_module, "_google_photos_permission_denial", lambda *_args: None)
    monkeypatch.setattr(
        app_module,
        "get_or_extract_google_photos_url",
        lambda *_args, **_kwargs: (None, "wait_timeout"),
    )

    with app.test_request_context(f"/extract-google-photos-url?url={share_url}"):
        response = app.view_functions["extract_google_photos_url"]()

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "Google Photos preview extraction is still in progress. Retry shortly."
    }
    assert response.headers["Retry-After"] == "5"


def test_google_photos_extraction_success_is_cached_until_expiry(monkeypatch):
    share_url = "https://photos.google.com/share/cache-expiry-test"
    image_url = "https://lh3.googleusercontent.com/pw/cache-expiry-image=w1200"
    clock = [100.0]
    monkeypatch.setattr(
        app_module,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )
    with app_module._cache_lock:
        app_module._url_cache.pop(share_url, None)

    try:
        app_module.cache_url(share_url, image_url)
        assert app_module.get_cached_url(share_url) == image_url
        clock[0] += app_module.GOOGLE_PHOTOS_URL_CACHE_TTL_SECONDS + 1
        assert app_module.get_cached_url(share_url) is None
    finally:
        with app_module._cache_lock:
            app_module._url_cache.pop(share_url, None)


def test_google_photos_user_retry_refreshes_a_cached_preview(monkeypatch):
    share_url = "https://photos.google.com/share/force-refresh-test"
    old_image_url = "https://lh3.googleusercontent.com/pw/old-image=w1200"
    refreshed_image_url = "https://lh3.googleusercontent.com/pw/refreshed-image=w1200"
    monkeypatch.setattr(
        app_module,
        "_extract_google_photos_image",
        lambda _url: refreshed_image_url,
    )
    with app_module._cache_lock:
        app_module._url_cache.pop(share_url, None)
        app_module._url_failure_cache.pop(share_url, None)
    app_module.cache_url(share_url, old_image_url)

    try:
        assert app_module.get_or_extract_google_photos_url(
            share_url, force_refresh=True
        ) == (refreshed_image_url, "miss")
        assert app_module.get_or_extract_google_photos_url(share_url) == (
            refreshed_image_url,
            "hit",
        )
    finally:
        with app_module._cache_lock:
            app_module._url_cache.pop(share_url, None)
            app_module._url_failure_cache.pop(share_url, None)


def test_google_photos_concurrent_extractions_share_one_inflight_request(monkeypatch):
    share_url = "https://photos.google.com/share/inflight-dedup-test"
    image_url = "https://lh3.googleusercontent.com/pw/inflight-image=w1200"
    barrier = threading.Barrier(4)
    extraction_calls = []
    monkeypatch.setattr(
        app_module,
        "_extract_google_photos_image",
        lambda _url: (extraction_calls.append(_url), time.sleep(0.05), image_url)[2],
    )
    with app_module._cache_lock:
        app_module._url_cache.pop(share_url, None)
        app_module._url_failure_cache.pop(share_url, None)

    def resolve_preview():
        barrier.wait(timeout=2)
        return app_module.get_or_extract_google_photos_url(share_url)[0]

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _index: resolve_preview(), range(4)))
        assert results == [image_url] * 4
        assert extraction_calls == [share_url]
    finally:
        with app_module._cache_lock:
            app_module._url_cache.pop(share_url, None)
            app_module._url_failure_cache.pop(share_url, None)


def test_google_photos_inflight_wait_timeout_is_bounded(monkeypatch):
    share_url = "https://photos.google.com/share/inflight-timeout-test"
    waited = []

    class TimedOutEvent:
        def wait(self, timeout):
            waited.append(timeout)
            return False

    with app_module._cache_lock:
        app_module._url_cache.pop(share_url, None)
        app_module._url_failure_cache.pop(share_url, None)
        app_module._url_inflight[share_url] = TimedOutEvent()

    try:
        assert app_module.get_or_extract_google_photos_url(share_url) == (
            None,
            "wait_timeout",
        )
        assert waited == [5]
    finally:
        with app_module._cache_lock:
            app_module._url_inflight.pop(share_url, None)


def test_google_photos_failure_is_cached_only_briefly(monkeypatch):
    share_url = "https://photos.google.com/share/failure-cache-test"
    extraction_calls = []
    monkeypatch.setattr(
        app_module,
        "_extract_google_photos_image",
        lambda _url: (extraction_calls.append(_url), None)[1],
    )
    with app_module._cache_lock:
        app_module._url_cache.pop(share_url, None)
        app_module._url_failure_cache.pop(share_url, None)

    try:
        assert app_module.get_or_extract_google_photos_url(share_url) == (None, "miss")
        assert app_module.get_or_extract_google_photos_url(share_url) == (None, "failure_hit")
        assert app_module.get_or_extract_google_photos_url(
            share_url, force_refresh=True
        ) == (None, "miss")
        assert extraction_calls == [share_url, share_url]
    finally:
        with app_module._cache_lock:
            app_module._url_failure_cache.pop(share_url, None)
