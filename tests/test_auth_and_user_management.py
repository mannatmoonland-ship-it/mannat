import pytest
from google.api_core.exceptions import DeadlineExceeded
from werkzeug.security import generate_password_hash

from app import FirebaseFirestoreStore, InMemoryFirestoreStore, app


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


class FakeUserStore:
    def __init__(self):
        self.users = {}
        self.custom_staff_types = {}
        self.audit_log = []
        self.fail_user_deletion = False

    def get_user_by_username(self, username):
        for user in self.users.values():
            if user.get("username", "").lower() == str(username).lower():
                return user
        return None

    def get_user(self, user_id):
        return self.users.get(user_id)

    def list_users(self):
        return list(self.users.values())

    def save_user(self, user):
        self.users[user["id"]] = user
        return self.users[user["id"]]

    def list_custom_staff_types(self):
        return list(self.custom_staff_types.values())

    def delete_user(self, user_id):
        if self.fail_user_deletion:
            raise RuntimeError("simulated Firestore delete failure")
        return self.users.pop(user_id, None) is not None

    def append_audit(self, action):
        self.audit_log.append(action)


@pytest.fixture
def auth_client(monkeypatch):
    store = FakeUserStore()
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "auth-tests-only-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    with app.test_client() as client:
        original_post = client.post

        def post_with_authenticated_form_csrf(*args, **kwargs):
            path = str(args[0]) if args else str(kwargs.get("path", ""))
            if path.startswith("/user-management/"):
                client.get("/user-management")
                with client.session_transaction() as flask_session:
                    csrf_token = flask_session.get("authenticated_form_csrf", "")
                form_data = kwargs.get("data")
                kwargs["data"] = {
                    **(form_data if isinstance(form_data, dict) else {}),
                    "csrf_token": csrf_token,
                }
            return original_post(*args, **kwargs)

        monkeypatch.setattr(client, "post", post_with_authenticated_form_csrf)
        yield client, store


def _save_test_user(store, user_id, username, role, session_id=None, password="Test-password-42!"):
    return store.save_user({
        "id": user_id,
        "username": username,
        "full_name": username,
        "role": role,
        "status": "active",
        "is_active": True,
        "active_sessions": [session_id] if session_id else [],
        "authorization_version": 1,
        "must_change_password": False,
        "password_hash": generate_password_hash(password),
    })


def _authenticate_user(client, user_id, session_id):
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id
        flask_session["session_id"] = session_id
        flask_session["authenticated_form_csrf"] = f"test-csrf-{session_id}"
        flask_session["authenticated_form_csrf_binding"] = f"{user_id}:{session_id}"


def test_login_succeeds_with_isolated_user_store(auth_client):
    client, store = auth_client
    _save_test_user(store, "login-user", "login-user", "office_staff")

    response = client.post(
        "/login",
        data={"username": "login-user", "password": "Test-password-42!"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    with client.session_transaction() as flask_session:
        assert flask_session.get("user_id") == "login-user"


def test_invalid_login_credentials_are_rejected(auth_client):
    client, store = auth_client
    _save_test_user(store, "login-user", "login-user", "office_staff")

    response = client.post(
        "/login",
        data={"username": "login-user", "password": "incorrect-password"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    with client.session_transaction() as flask_session:
        assert "user_id" not in flask_session


def test_login_firestore_timeout_returns_temporary_error_without_logging_credentials(
    auth_client, monkeypatch, caplog
):
    client, store = auth_client

    def timeout_lookup(_username):
        raise DeadlineExceeded("Firestore request timed out")

    monkeypatch.setattr(store, "get_user_by_username", timeout_lookup)
    caplog.set_level("INFO", logger="app")

    response = client.post(
        "/login",
        data={"username": "private-test-user", "password": "private-test-password"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert b"Sign-in is temporarily unavailable" in response.data
    assert b"Invalid username or password" not in response.data
    assert "Login POST received" in caplog.text
    assert "timed out during sign-in" in caplog.text
    assert "private-test-user" not in caplog.text
    assert "private-test-password" not in caplog.text


def test_firestore_login_lookup_uses_field_filter_and_bounded_retry():
    class FakeQuery:
        def where(self, *, filter):
            assert (filter.field_path, filter.op_string) == ("username_lower", "==")
            return self

        def limit(self, count):
            assert count == 1
            return self

        def stream(self, *, retry, timeout):
            assert retry.timeout == 5
            assert timeout == 5
            return iter(())

    class FakeCollection:
        def where(self, **_kwargs):
            return FakeQuery()

    class FakeClient:
        def collection(self, name):
            assert name == "users"
            return FakeCollection()

    store = FirebaseFirestoreStore(client=FakeClient(), project_id="isolated-test-project")

    assert store.get_user_by_username("nobody") is None


def test_user_deletion_persists_after_user_management_is_reopened(auth_client):
    client, store = auth_client
    _save_test_user(store, "admin-user", "admin-user", "super_admin", "admin-session")
    _save_test_user(store, "deleted-user", "deleted-user", "office_staff", "deleted-session")
    _authenticate_user(client, "admin-user", "admin-session")

    page_response = client.get("/user-management")
    assert page_response.status_code == 200
    assert b'action="/user-management/deleted-user/delete"' in page_response.data
    assert b"Delete this user account?" in page_response.data

    response = client.post(
        "/user-management/deleted-user/delete",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert store.get_user("deleted-user") is None
    assert b"was removed." in response.data
    assert b"<td>deleted-user</td>" not in response.data
    reopened = client.get("/user-management")
    assert reopened.status_code == 200
    assert b"<td>deleted-user</td>" not in reopened.data
    assert store.get_user("deleted-user") is None
    assert any(
        entry.get("action") == "user_deleted"
        and entry.get("target_user_id") == "deleted-user"
        for entry in store.audit_log
    )
    with app.test_client() as deleted_user_client:
        _authenticate_user(deleted_user_client, "deleted-user", "deleted-session")
        session_response = deleted_user_client.get("/", follow_redirects=False)
    assert session_response.status_code == 302
    assert "/login" in session_response.headers["Location"]


def test_user_deletion_failure_does_not_report_success_or_audit(auth_client):
    client, store = auth_client
    _save_test_user(store, "admin-user", "admin-user", "super_admin", "admin-session")
    target = _save_test_user(store, "kept-user", "kept-user", "office_staff")
    _authenticate_user(client, "admin-user", "admin-session")
    store.fail_user_deletion = True

    response = client.post(
        "/user-management/kept-user/delete",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"User could not be deleted. Please try again." in response.data
    assert b"was removed." not in response.data
    assert store.get_user(target["id"]) is not None
    assert not any(entry.get("action") == "user_deleted" for entry in store.audit_log)


def test_user_deletion_preserves_self_deletion_and_role_protections(auth_client):
    client, store = auth_client
    admin = _save_test_user(store, "admin-user", "admin-user", "super_admin", "admin-session")
    target = _save_test_user(store, "protected-user", "protected-user", "office_staff")
    _authenticate_user(client, admin["id"], "admin-session")

    self_delete = client.post(
        f"/user-management/{admin['id']}/delete",
        follow_redirects=True,
    )
    assert self_delete.status_code == 200
    assert b"You cannot delete your own account while signed in." in self_delete.data
    assert store.get_user(admin["id"]) is not None

    staff = _save_test_user(store, "staff-user", "staff-user", "office_staff", "staff-session")
    _authenticate_user(client, staff["id"], "staff-session")
    denied = client.post(
        f"/user-management/{target['id']}/delete",
        follow_redirects=True,
    )
    assert denied.status_code == 200
    assert b"You do not have permission to access that page." in denied.data
    assert store.get_user(target["id"]) is not None


def test_deleting_another_super_admin_leaves_one_active_super_admin(auth_client):
    client, store = auth_client
    admin = _save_test_user(store, "admin-user", "admin-user", "super_admin", "admin-session")
    target = _save_test_user(store, "second-admin", "second-admin", "super_admin")
    _authenticate_user(client, admin["id"], "admin-session")

    response = client.post(
        f"/user-management/{target['id']}/delete",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert store.get_user(target["id"]) is None
    remaining_admins = [
        user for user in store.list_users()
        if user.get("role") == "super_admin" and user.get("is_active")
    ]
    assert [user["id"] for user in remaining_admins] == [admin["id"]]


def test_user_deletion_requires_authenticated_form_csrf(auth_client):
    _client, store = auth_client
    target = _save_test_user(store, "protected-user", "protected-user", "office_staff")
    admin = _save_test_user(store, "admin-user", "admin-user", "super_admin", "admin-session")

    with app.test_client() as raw_client:
        _authenticate_user(raw_client, admin["id"], "admin-session")
        response = raw_client.post(f"/user-management/{target['id']}/delete")

    assert response.status_code == 400
    assert store.get_user(target["id"]) is not None


def test_firestore_user_deletion_targets_users_collection_and_refetch_excludes_user():
    class Snapshot:
        def __init__(self, reference):
            self.exists = reference.document_id in reference.collection.records

    class DocumentReference:
        def __init__(self, collection, document_id):
            self.collection = collection
            self.document_id = document_id

        def get(self):
            return Snapshot(self)

        def delete(self):
            self.collection.records.pop(self.document_id, None)

    class CollectionReference:
        def __init__(self, records):
            self.records = records
            self.requested_document_ids = []

        def document(self, document_id):
            self.requested_document_ids.append(document_id)
            return DocumentReference(self, document_id)

        def stream(self):
            return [
                type(
                    "UserSnapshot",
                    (),
                    {
                        "id": document_id,
                        "to_dict": lambda self, document=document: document,
                    },
                )()
                for document_id, document in self.records.items()
            ]

    class FirestoreClient:
        def __init__(self):
            self.users = CollectionReference({
                "deleted-test-user": {"username": "deleted-test-user"},
                "kept-test-user": {"username": "kept-test-user"},
            })

        def collection(self, name):
            assert name == "users"
            return self.users

    firestore_client = FirestoreClient()
    store = FirebaseFirestoreStore(client=firestore_client, project_id="isolated-test-project")

    assert store.delete_user("deleted-test-user") is True
    assert firestore_client.users.requested_document_ids == ["deleted-test-user"]
    assert [user["id"] for user in store.list_users()] == ["kept-test-user"]
    assert "deleted-test-user" not in firestore_client.users.records
