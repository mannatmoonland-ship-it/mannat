import pytest

from app import InMemoryFirestoreStore, app


@pytest.fixture
def daily_report_client(monkeypatch):
    store = InMemoryFirestoreStore()
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "daily-reports-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    return store, app.test_client()


def _add_active_user(store, *, user_id, role, session_id, custom_staff_type=""):
    store.save_user({
        "id": user_id,
        "username": user_id,
        "role": role,
        "custom_staff_type": custom_staff_type,
        "status": "active",
        "is_active": True,
        "active_sessions": [session_id],
    })


def _authenticate(client, user_id, session_id):
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id
        flask_session["session_id"] = session_id


def test_daily_report_routes_reject_anonymous_access_before_reading_data(daily_report_client, monkeypatch):
    _store, client = daily_report_client
    monkeypatch.setattr(
        "app._get_daily_record_for_date",
        lambda _date: pytest.fail("Anonymous requests must not read Daily Reports."),
    )

    for path in ("/daily-reports?date=2026-10-03", "/daily-reports/share-image?date=2026-10-03"):
        response = client.get(path, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        assert response.status_code == 401
        assert "<svg" not in response.get_data(as_text=True)


def test_daily_report_routes_deny_authenticated_users_without_read_permission(daily_report_client, monkeypatch):
    store, client = daily_report_client
    _add_active_user(
        store,
        user_id="report-user",
        role="Report Viewer",
        custom_staff_type="Report Viewer",
        session_id="report-session",
    )
    store.save_custom_staff_type({
        "id": "report-viewer",
        "name": "Report Viewer",
        "permissions": [],
    })
    _authenticate(client, "report-user", "report-session")
    monkeypatch.setattr(
        "app._get_daily_record_for_date",
        lambda _date: pytest.fail("Unauthorized requests must not read Daily Reports."),
    )

    for path in ("/daily-reports", "/daily-reports/share-image"):
        response = client.get(path)
        assert response.status_code == 403


def test_daily_report_reader_can_open_page_and_share_image(daily_report_client, monkeypatch):
    store, client = daily_report_client
    _add_active_user(
        store,
        user_id="report-user",
        role="super_admin",
        session_id="report-session",
    )
    monkeypatch.setattr(
        "app._get_daily_record_for_date",
        lambda _date: {"date": "2026-10-03", "activity": "Authorized report data"},
    )
    _authenticate(client, "report-user", "report-session")

    page_response = client.get("/daily-reports")
    image_response = client.get("/daily-reports/share-image?date=2026-10-03")

    assert page_response.status_code == 200
    assert page_response.headers["Cache-Control"] == "private, no-store"
    with client.session_transaction() as flask_session:
        assert flask_session.get("daily_reports_csrf")
    assert image_response.status_code == 200
    assert image_response.mimetype == "image/svg+xml"
    assert "Authorized report data" in image_response.get_data(as_text=True)
    assert image_response.headers["Cache-Control"] == "private, no-store"
