from pathlib import Path


def test_daily_reports_drive_images_use_direct_image_endpoints_and_no_referrer():
    javascript = (
        Path(__file__).parents[1] / "static" / "daily_reports.js"
    ).read_text(encoding="utf-8")

    # Verify modern Google Drive image candidates
    assert "https://lh3.googleusercontent.com/d/${encoded}" in javascript
    assert "https://lh3.googleusercontent.com/d/${encoded}=w1600" in javascript
    assert "https://drive.google.com/thumbnail?id=${encoded}&sz=w1600" in javascript

    # Verify previewHtml renders <img> for Photo type with referrerpolicy="no-referrer"
    assert 'd && type==="Photo"' in javascript
    assert 'referrerpolicy="no-referrer"' in javascript
    assert 'data-fallbacks=' in javascript
    assert 'Photo preview unavailable. Make sure link sharing is set to' in javascript

    # Verify iframe is preserved for non-photo Google Drive files
    assert 'if(d) return `<iframe src="https://drive.google.com/file/d/${d}/preview"></iframe>`;' in javascript

    # Verify print markup sets no-referrer and uses drive candidates
    assert 'img.referrerPolicy = "no-referrer";' in javascript
    assert 'driveCandidates = item.type === "Photo" ? getDriveImageCandidates(imgUrl) : [];' in javascript
