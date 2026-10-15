from pathlib import Path


ROOT = Path(__file__).parents[1]
DAILY_REPORTS_JS = (ROOT / "static/daily_reports.js").read_text(encoding="utf-8")
LEGACY_DAILY_REPORTS_JS = (ROOT / "static/old_daily_reports.js").read_text(encoding="utf-8")
DAILY_REPORTS_TEMPLATE = (ROOT / "templates/daily_reports.html").read_text(encoding="utf-8")


def source_between(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def test_daily_report_delete_uses_flask_api_and_not_browser_firestore_delete():
    delete_source = source_between(
        DAILY_REPORTS_JS,
        "async function confirmDeleteRecord()",
        "function cancelDelete()",
    )

    assert 'method: "DELETE"' in delete_source
    assert "`/api/daily-records/${encodeURIComponent(deletingId)}`" in delete_source
    assert '"X-CSRF-Token": document.body.dataset.dailyReportsCsrf || ""' in delete_source
    assert "deleteDoc" not in DAILY_REPORTS_JS
    assert "deleteDoc" not in LEGACY_DAILY_REPORTS_JS
    assert "`/api/daily-records/${encodeURIComponent(deletingId)}`" in LEGACY_DAILY_REPORTS_JS


def test_daily_report_delete_confirmation_and_success_refresh_remain():
    confirm_source = source_between(
        DAILY_REPORTS_JS,
        "function openDeleteConfirm(id)",
        "async function confirmDeleteRecord()",
    )
    delete_source = source_between(
        DAILY_REPORTS_JS,
        "async function confirmDeleteRecord()",
        "function cancelDelete()",
    )

    assert "registerEscClose(deleteModal, cancelDelete)" in confirm_source
    assert 'if (!response.ok)' in delete_source
    assert "const errorResponse = await response.json()" in delete_source
    assert "errorMessage = errorResponse.error" in delete_source
    assert delete_source.index("if (!response.ok)") < delete_source.index("deletingId = null")
    assert delete_source.index("deletingId = null") < delete_source.index('showToast("Record deleted successfully."')
    assert delete_source.index('showToast("Record deleted successfully."') < delete_source.index("await loadRecords()")
    assert "error instanceof Error ? error.message" in delete_source
    assert "Unable to delete the daily report. Please try again." in delete_source


def test_daily_reports_page_exposes_session_csrf_for_delete_request():
    assert '<body data-daily-reports-csrf="{{ daily_reports_csrf }}">' in DAILY_REPORTS_TEMPLATE
