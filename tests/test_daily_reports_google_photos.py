from pathlib import Path


def test_google_photos_preview_has_bounded_retryable_failure_handling():
    javascript = (
        Path(__file__).parents[1] / "static" / "daily_reports.js"
    ).read_text(encoding="utf-8")

    assert "GOOGLE_PHOTOS_EXTRACTION_TIMEOUT_MS = 15000" in javascript
    assert "GOOGLE_PHOTOS_PREVIEW_WAIT_TIMEOUT_MS = 15000" in javascript
    assert "controller.abort();" in javascript
    assert 'class="dr-google-photos-retry"' in javascript
    assert 'class="dr-ev-preview-loading"' in javascript
    assert "Google Photos preview unavailable. Retry or open the original." in javascript
    assert 'target="_blank" rel="noopener">Open original</a>' in javascript
    assert 'addEventListener("click", retryGooglePhotosPreview)' in javascript
    assert "Google Photos direct URL obtained:" not in javascript


def test_google_photos_previews_deduplicate_and_bound_extraction_work():
    javascript = (
        Path(__file__).parents[1] / "static" / "daily_reports.js"
    ).read_text(encoding="utf-8")

    assert "const googlePhotosExtractionCache = new Map();" in javascript
    assert "const GOOGLE_PHOTOS_FAILURE_CACHE_MS = 30000;" in javascript
    assert "const GOOGLE_PHOTOS_MAX_CONCURRENT_EXTRACTIONS = 3;" in javascript
    assert 'retry ? "&retry=1" : ""' in javascript
    assert "const responseBody = await response.text();" in javascript
    assert "new IntersectionObserver(" in javascript
    assert '{ rootMargin: "400px 0px" }' in javascript
    assert "image.loading = \"lazy\";" in javascript
    assert "pendingRequests" not in javascript


def test_google_photos_preview_accepts_current_plain_text_api_and_json_responses():
    javascript = (
        Path(__file__).parents[1] / "static" / "daily_reports.js"
    ).read_text(encoding="utf-8")

    assert 'response.headers.get("content-type")' in javascript
    assert 'contentType.includes("application/json")' in javascript
    assert "JSON.parse(responseBody)" in javascript
    assert "if (!isGooglePhotosPreviewUrl(directUrl))" in javascript
    assert "return directUrl;" in javascript
