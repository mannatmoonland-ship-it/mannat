from pathlib import Path


def test_google_photos_preview_has_bounded_retryable_failure_handling():
    javascript = (
        Path(__file__).parents[1] / "static" / "daily_reports.js"
    ).read_text(encoding="utf-8")

    assert "GOOGLE_PHOTOS_EXTRACTION_TIMEOUT_MS = 25000" in javascript
    assert "controller.abort();" in javascript
    assert 'class="dr-google-photos-retry"' in javascript
    assert 'class="dr-ev-preview-loading"' in javascript
    assert 'target="_blank" rel="noopener">Open original</a>' in javascript
    assert 'addEventListener("click", retryGooglePhotosPreview)' in javascript
    assert "Google Photos direct URL obtained:" not in javascript
