import pytest
from werkzeug.security import generate_password_hash

from app import InMemoryFirestoreStore, app


class FakeRecordSnapshot:
    def __init__(self, exists):
        self.exists = exists


class FakeRecordReference:
    def __init__(self, collection, record_id):
        self.collection = collection
        self.id = record_id

    def get(self):
        return FakeRecordSnapshot(self.id in self.collection.records)

    def delete(self):
        self.collection.deleted_ids.append(self.id)
        self.collection.records.pop(self.id, None)


class FakeDailyRecordsCollection:
    def __init__(self):
        self.records = {
            "report-1": {
                "date": "2026-10-07",
                "evidence": [
                    {"type": "Google Photos", "url": "https://photos.example/report-photo"},
                    {"type": "YouTube", "url": "https://youtube.example/report-video"},
                ],
            },
            "report-2": {"date": "2026-10-06", "notes": "Keep this record"},
        }
        self.deleted_ids = []

    def document(self, record_id):
        return FakeRecordReference(self, record_id)


@pytest.fixture
def daily_report_delete_client(monkeypatch):
    store = InMemoryFirestoreStore()
    user = store.save_user({
        "id": "staff-user",
        "username": "staff-user",
        "role": "super_admin",
        "status": "active",
        "is_active": True,
        "active_sessions": ["active-session"],
        "password_hash": generate_password_hash("CurrentSecure1!"),
    })
    records = FakeDailyRecordsCollection()
    unrelated_collection = {"keep": {"value": True}}
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "daily-report-delete-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    monkeypatch.setattr("app.get_daily_records_collection", lambda: records)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user["id"]
        flask_session["role"] = user["role"]
        flask_session["session_id"] = "active-session"
        flask_session["daily_reports_csrf"] = "daily-reports-csrf-token"
    return store, client, records, unrelated_collection


def set_trusted_role(store, role, *, permissions=None):
    user = store.get_user("staff-user")
    user["role"] = role
    user["custom_staff_type"] = role if role not in {"super_admin", "office_staff", "supervisor"} else ""
    store.save_user(user)
    if permissions is not None:
        store.save_custom_staff_type({
            "id": "custom-role",
            "name": role,
            "permissions": permissions,
        })


def delete_report(client, record_id="report-1", *, csrf="daily-reports-csrf-token", json=None):
    headers = {}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return client.delete(f"/api/daily-records/{record_id}", headers=headers, json=json)


def test_unauthenticated_daily_report_delete_is_rejected(daily_report_delete_client):
    _store, client, records, _unrelated = daily_report_delete_client
    with client.session_transaction() as flask_session:
        flask_session.clear()

    response = delete_report(client)

    assert response.status_code == 401
    assert "report-1" in records.records
    assert records.deleted_ids == []


@pytest.mark.parametrize("role", ["office_staff", "supervisor"])
def test_operational_staff_cannot_delete_daily_reports_by_default(daily_report_delete_client, role):
    store, client, records, _unrelated = daily_report_delete_client
    set_trusted_role(store, role)

    response = delete_report(client)

    assert response.status_code == 403
    assert response.get_json() == {
        "error": (
            "You do not have the required privileges to delete this daily record. "
            "Please contact a Super Admin or Admin to request deletion."
        )
    }
    assert "report-1" in records.records
    assert records.deleted_ids == []


def test_custom_role_without_delete_permission_is_denied(daily_report_delete_client):
    store, client, records, _unrelated = daily_report_delete_client
    set_trusted_role(store, "Report Operator", permissions=["daily_reports.read"])

    response = delete_report(client)

    assert response.status_code == 403
    assert "report-1" in records.records


def test_super_admin_can_delete_requested_daily_report_only(daily_report_delete_client):
    _store, client, records, unrelated_collection = daily_report_delete_client

    response = delete_report(client)

    assert response.status_code == 200
    assert response.get_json() == {"id": "report-1"}
    assert "report-1" not in records.records
    assert records.records["report-2"] == {"date": "2026-10-06", "notes": "Keep this record"}
    assert records.deleted_ids == ["report-1"]
    assert unrelated_collection == {"keep": {"value": True}}
    assert response.headers["Cache-Control"] == "no-store"


def test_custom_role_can_delete_with_explicit_permission(daily_report_delete_client):
    store, client, records, _unrelated = daily_report_delete_client
    set_trusted_role(store, "Report Maintainer", permissions=["daily_reports.delete"])

    response = delete_report(client)

    assert response.status_code == 200
    assert records.deleted_ids == ["report-1"]


@pytest.mark.parametrize(
    ("method", "path", "permissions"),
    [
        ("POST", "/api/daily-records", ["daily_reports.read"]),
        ("PATCH", "/api/daily-records/report-1", ["daily_reports.read"]),
        ("GET", "/api/daily-records/dates?start=2026-10-01&end=2026-10-31", ["daily_reports.create"]),
        ("GET", "/api/daily-records/author-avatars?user_id=staff-user", ["daily_reports.create"]),
    ],
)
def test_daily_report_apis_require_the_matching_compiled_permission(
    daily_report_delete_client,
    method,
    path,
    permissions,
):
    store, client, records, _unrelated = daily_report_delete_client
    set_trusted_role(store, "Report Operator", permissions=permissions)

    response = client.open(
        path,
        method=method,
        json={"date": "2026-10-07", "activity": "Foundation work"},
    )

    assert response.status_code == 403
    assert records.deleted_ids == []
    assert "report-1" in records.records


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/daily-records"),
        ("PATCH", "/api/daily-records/report-1"),
    ],
)
def test_daily_report_create_and_edit_require_csrf(
    daily_report_delete_client, method, path
):
    _store, client, records, _unrelated = daily_report_delete_client
    with client.session_transaction() as flask_session:
        flask_session.pop("daily_reports_csrf", None)

    response = client.open(
        path,
        method=method,
        json={"date": "2026-10-07", "activity": "Foundation work"},
    )

    assert response.status_code == 403
    assert "report-1" in records.records
    assert records.deleted_ids == []


def test_daily_report_delete_requires_csrf(daily_report_delete_client):
    _store, client, records, _unrelated = daily_report_delete_client

    response = delete_report(client, csrf="wrong-token")

    assert response.status_code == 403
    assert "report-1" in records.records
    assert records.deleted_ids == []


@pytest.mark.parametrize(
    "forged_payload",
    [
        {"role": "super_admin"},
        {"permissions": ["daily_reports.delete"]},
        {"user_id": "another-super-admin"},
    ],
)
def test_browser_supplied_authorization_data_cannot_elevate_staff(
    daily_report_delete_client,
    forged_payload,
):
    store, client, records, _unrelated = daily_report_delete_client
    set_trusted_role(store, "office_staff")
    with client.session_transaction() as flask_session:
        flask_session["role"] = "super_admin"

    response = delete_report(client, json=forged_payload)

    assert response.status_code == 403
    assert "report-1" in records.records
    assert records.deleted_ids == []


def test_nonexistent_daily_report_returns_safe_not_found(daily_report_delete_client):
    _store, client, records, _unrelated = daily_report_delete_client

    response = delete_report(client, "missing-report")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Daily Report not found."}
    assert records.deleted_ids == []


@pytest.mark.parametrize("record_id", ["report-1/nested", "..", "x" * 1501])
def test_invalid_daily_report_id_or_path_is_rejected(daily_report_delete_client, record_id):
    _store, client, records, _unrelated = daily_report_delete_client

    response = delete_report(client, record_id)

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid Daily Report ID."}
    assert records.deleted_ids == []


def test_delete_does_not_touch_external_evidence_or_other_collections(daily_report_delete_client, monkeypatch):
    _store, client, records, unrelated_collection = daily_report_delete_client
    external_requests = []
    monkeypatch.setattr("app.requests.get", lambda *args, **kwargs: external_requests.append(args))

    response = delete_report(client)

    assert response.status_code == 200
    assert records.deleted_ids == ["report-1"]
    assert unrelated_collection == {"keep": {"value": True}}
    assert external_requests == []
