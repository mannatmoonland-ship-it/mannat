from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import DeadlineExceeded
from werkzeug.security import check_password_hash, generate_password_hash

import app as app_module
from app import FirebaseFirestoreStore, app

BOOTSTRAP_TEST_PASSWORD = "Test-Only-Admin-2026!"


class FakeFirestoreStore:
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

    def save_custom_staff_type(self, staff_type):
        self.custom_staff_types[staff_type["id"]] = staff_type

    def delete_custom_staff_type(self, staff_type_id):
        self.custom_staff_types.pop(staff_type_id, None)

    def delete_user(self, user_id):
        if self.fail_user_deletion:
            raise RuntimeError("simulated Firestore delete failure")
        return self.users.pop(user_id, None) is not None

    def append_audit(self, action):
        self.audit_log.append(action)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INITIAL_SUPER_ADMIN_PASSWORD", BOOTSTRAP_TEST_PASSWORD)
    app.config.update(TESTING=True, SECRET_KEY="test-secret", FIRESTORE_STORE=FakeFirestoreStore())
    with app.test_client() as c:
        original_post = c.post

        def post_with_authenticated_form_csrf(*args, **kwargs):
            path = str(args[0]) if args else str(kwargs.get("path", ""))
            if path.startswith((
                "/logout",
                "/test-email",
                "/change-password",
                "/forgot-password",
                "/user-management/",
            )) or path == "/user-management/custom-type":
                c.get("/user-management")
                with c.session_transaction() as flask_session:
                    csrf_token = flask_session.get("authenticated_form_csrf", "")
                form_data = kwargs.get("data")
                if isinstance(form_data, dict):
                    kwargs["data"] = {**form_data, "csrf_token": csrf_token}
                else:
                    kwargs["data"] = {"csrf_token": csrf_token}
            return original_post(*args, **kwargs)

        monkeypatch.setattr(c, "post", post_with_authenticated_form_csrf)
        yield c


def test_ensure_initial_super_admin_is_a_noop_without_bootstrap_secret(client, monkeypatch):
    from app import ensure_initial_super_admin

    monkeypatch.delenv("INITIAL_SUPER_ADMIN_PASSWORD", raising=False)
    store = app.config["FIRESTORE_STORE"]
    before = {key: dict(value) for key, value in store.users.items()}

    ensure_initial_super_admin()

    assert store.users == before
    assert store.get_user_by_username("Rahim") is None


def test_initial_super_admin_bootstrap_requires_explicit_strong_password(client, monkeypatch):
    from app import ensure_initial_super_admin

    store = app.config["FIRESTORE_STORE"]
    monkeypatch.setenv("INITIAL_SUPER_ADMIN_PASSWORD", "Secure-bootstrap-2026!")

    user = ensure_initial_super_admin()

    assert user["role"] == "super_admin"
    assert user["must_change_password"] is True
    assert user["password_hash"] != BOOTSTRAP_TEST_PASSWORD
    assert store.get_user_by_username("Rahim")["id"] == user["id"]


def test_initial_super_admin_bootstrap_rejects_weak_password(client, monkeypatch):
    from app import ensure_initial_super_admin

    store = app.config["FIRESTORE_STORE"]
    monkeypatch.setenv("INITIAL_SUPER_ADMIN_PASSWORD", "weak")

    assert ensure_initial_super_admin() is None
    assert store.users == {}


def test_user_deletion_does_not_recreate_deleted_account(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    login_response = client.post(
        "/login",
        data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    store = app.config["FIRESTORE_STORE"]
    store.save_user({
        "id": "deleted-user",
        "username": "deleted-user",
        "full_name": "Deleted User",
        "role": "office_staff",
        "is_active": True,
        "active_sessions": ["deleted-user-session"],
        "authorization_version": 1,
        "password_hash": generate_password_hash("User-test-password-42!"),
    })

    response = client.post(
        "/user-management/deleted-user/delete",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert store.get_user("deleted-user") is None
    assert all(user["id"] != "deleted-user" for user in store.list_users())
    assert b"was removed." in response.data

    reopened = client.get("/user-management")
    assert reopened.status_code == 200
    assert b"<td>deleted-user</td>" not in reopened.data
    assert all(user["id"] != "deleted-user" for user in store.list_users())
    assert any(
        entry.get("action") == "user_deleted"
        and entry.get("target_user_id") == "deleted-user"
        for entry in store.audit_log
    )
    with app.test_client() as deleted_user_client:
        with deleted_user_client.session_transaction() as deleted_session:
            deleted_session["user_id"] = "deleted-user"
            deleted_session["session_id"] = "deleted-user-session"
        session_response = deleted_user_client.get("/", follow_redirects=False)
    assert session_response.status_code == 302
    assert "/login" in session_response.headers["Location"]


def test_user_deletion_failure_shows_error_without_audit_or_removing_user(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    login_response = client.post(
        "/login",
        data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    store = app.config["FIRESTORE_STORE"]
    target = store.save_user({
        "id": "undeleted-user",
        "username": "undeleted-user",
        "full_name": "Undeleted User",
        "role": "office_staff",
        "is_active": True,
        "active_sessions": [],
        "authorization_version": 1,
        "password_hash": generate_password_hash("User-test-password-42!"),
    })
    store.fail_user_deletion = True

    response = client.post(
        "/user-management/undeleted-user/delete",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"User could not be deleted. Please try again." in response.data
    assert b"was removed." not in response.data
    assert store.get_user(target["id"]) is not None
    assert not any(entry.get("action") == "user_deleted" for entry in store.audit_log)


def test_user_deletion_preserves_self_deletion_protection(client):
    from app import ensure_initial_super_admin

    admin = ensure_initial_super_admin()
    login_response = client.post(
        "/login",
        data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    response = client.post(
        f"/user-management/{admin['id']}/delete",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"You cannot delete your own account while signed in." in response.data
    assert app.config["FIRESTORE_STORE"].get_user(admin["id"]) is not None


def test_user_deletion_requires_super_admin_authorization(client):
    store = app.config["FIRESTORE_STORE"]
    target = store.save_user({
        "id": "protected-user",
        "username": "protected-user",
        "full_name": "Protected User",
        "role": "office_staff",
        "is_active": True,
        "active_sessions": [],
        "authorization_version": 1,
        "password_hash": generate_password_hash("User-test-password-42!"),
    })
    actor = store.save_user({
        "id": "staff-actor",
        "username": "staff-actor",
        "full_name": "Staff Actor",
        "role": "office_staff",
        "is_active": True,
        "active_sessions": ["staff-session"],
        "authorization_version": 1,
        "password_hash": generate_password_hash("Actor-test-password-42!"),
    })
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = actor["id"]
        flask_session["session_id"] = "staff-session"
        flask_session["authenticated_form_csrf"] = "staff-session-csrf"
        flask_session["authenticated_form_csrf_binding"] = "staff-actor:staff-session"

    response = client.post(
        f"/user-management/{target['id']}/delete",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"You do not have permission to access that page." in response.data
    assert store.get_user(target["id"]) is not None


def test_firestore_user_deletion_targets_users_collection_and_is_visible_on_refetch():
    class Snapshot:
        def __init__(self, reference):
            self.exists = reference.document_id in reference.collection.records
            self._reference = reference

        def to_dict(self):
            return self._reference.collection.records.get(self._reference.document_id)

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
                type("UserSnapshot", (), {
                    "id": document_id,
                    "to_dict": lambda self, document=document: document,
                })()
                for document_id, document in self.records.items()
            ]

    class FirestoreClient:
        def __init__(self):
            self.users = CollectionReference({
                "deleted-test-user": {"username": "deleted-test-user"},
                "kept-test-user": {"username": "kept-test-user"},
            })
            self.collection_names = []

        def collection(self, name):
            self.collection_names.append(name)
            assert name == "users"
            return self.users

    from app import FirebaseFirestoreStore

    firestore_client = FirestoreClient()
    store = FirebaseFirestoreStore(client=firestore_client, project_id="isolated-test-project")

    assert store.delete_user("deleted-test-user") is True
    assert firestore_client.users.requested_document_ids == ["deleted-test-user"]
    assert [user["id"] for user in store.list_users()] == ["kept-test-user"]
    assert "deleted-test-user" not in firestore_client.users.records


def test_user_deletion_rejects_missing_csrf_token(client):
    store = app.config["FIRESTORE_STORE"]
    target = store.save_user({
        "id": "csrf-protected-user",
        "username": "csrf-protected-user",
        "full_name": "CSRF Protected User",
        "role": "office_staff",
        "is_active": True,
        "active_sessions": [],
        "authorization_version": 1,
        "password_hash": generate_password_hash("User-test-password-42!"),
    })
    admin = store.save_user({
        "id": "csrf-test-admin",
        "username": "csrf-test-admin",
        "full_name": "CSRF Test Admin",
        "role": "super_admin",
        "is_active": True,
        "active_sessions": ["csrf-test-session"],
        "authorization_version": 1,
        "password_hash": generate_password_hash("Admin-test-password-42!"),
    })
    with app.test_client() as csrf_client:
        with csrf_client.session_transaction() as flask_session:
            flask_session["user_id"] = admin["id"]
            flask_session["session_id"] = "csrf-test-session"
        response = csrf_client.post(f"/user-management/{target['id']}/delete")

    assert response.status_code == 400
    assert store.get_user(target["id"]) is not None


def test_firebase_service_account_can_be_discovered_from_launch_directory(tmp_path, monkeypatch):
    from app import find_firebase_service_account_path

    credential_file = tmp_path / "mannat-moon-construction-firebase-adminsdk-test.json"
    credential_file.touch()
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert find_firebase_service_account_path() == str(credential_file)


def test_office_staff_and_supervisor_share_full_operational_permissions(client):
    from app import ROLE_PERMISSIONS

    assert ROLE_PERMISSIONS["office_staff"] == ROLE_PERMISSIONS["supervisor"]
    assert "dashboard" in ROLE_PERMISSIONS["office_staff"]
    assert "daily_reports" in ROLE_PERMISSIONS["office_staff"]
    assert "trade_builder" in ROLE_PERMISSIONS["office_staff"]
    assert "drawings" in ROLE_PERMISSIONS["office_staff"]
    assert "labour_payments" in ROLE_PERMISSIONS["office_staff"]
    assert "ledger_prints" in ROLE_PERMISSIONS["office_staff"]
    assert "materials" in ROLE_PERMISSIONS["office_staff"]
    assert "labour_muster" in ROLE_PERMISSIONS["office_staff"]
    assert "camera_upload" not in ROLE_PERMISSIONS["office_staff"]
    assert "camera_upload" not in ROLE_PERMISSIONS["supervisor"]
    assert "camera_upload" not in ROLE_PERMISSIONS["super_admin"]
    assert "user_management" not in ROLE_PERMISSIONS["office_staff"]
    assert "super_admin_recovery" not in ROLE_PERMISSIONS["office_staff"]
    assert "user_management" not in ROLE_PERMISSIONS["supervisor"]
    assert "super_admin_recovery" not in ROLE_PERMISSIONS["supervisor"]


def test_dashboard_redirects_to_login_when_firestore_not_configured(client):
    import app as app_module

    original = app_module.get_user_store
    app_module.get_user_store = lambda: (_ for _ in ()).throw(RuntimeError("Firebase Admin SDK is not installed or Firestore is not configured."))

    try:
        with client.session_transaction() as sess:
            sess["user_id"] = "missing-user"
            sess["session_id"] = "missing-session"

        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["Location"] == "/login"
    finally:
        app_module.get_user_store = original


def test_initial_super_admin_setup(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    user = app.config["FIRESTORE_STORE"].get_user_by_username("Rahim")

    assert user is not None
    assert user["role"] == "super_admin"
    assert user["must_change_password"] is True
    assert user["username"] == "Rahim"
    assert user["password_hash"]


def test_super_admin_login_requires_password_change(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    response = client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"] == "/change-password"


def test_ensure_initial_super_admin_preserves_existing_users(client):
    from app import ensure_initial_super_admin

    store = app.config["FIRESTORE_STORE"]
    store.save_user({
        "id": "existing-user",
        "username": "Alice",
        "full_name": "Alice Existing",
        "email": "alice@example.com",
        "recovery_email": "",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": "hash-alice",
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "created_at": "now",
        "updated_at": "now",
        "permissions": ["dashboard"],
    })

    ensure_initial_super_admin()

    preserved = store.get_user("existing-user")
    assert preserved["username"] == "Alice"
    assert preserved["role"] == "office_staff"
    assert preserved["email"] == "alice@example.com"


def test_user_creation_prevents_duplicate_username(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    login_response = client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)
    assert login_response.status_code == 302

    with client.session_transaction() as sess:
        sess["user_id"] = app.config["FIRESTORE_STORE"].get_user_by_username("Rahim")["id"]
        sess["role"] = "super_admin"

    response = client.post(
        "/user-management/create",
        data={
            "username": "rahim",
            "full_name": "Rahim Duplicate",
            "role": "office_staff",
            "custom_staff_type": "",
            "email": "rahim@example.com",
            "recovery_email": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_new_user_receives_unique_password_setup_link_instead_of_shared_password(client, monkeypatch):
    store = app.config["FIRESTORE_STORE"]
    store.save_user({
        "id": "admin-id",
        "username": "Admin",
        "full_name": "Admin",
        "role": "super_admin",
        "is_active": True,
        "active_sessions": ["admin-session"],
    })
    with client.session_transaction() as session:
        session["user_id"] = "admin-id"
        session["session_id"] = "admin-session"
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    response = client.post("/user-management/create", data={
        "username": "new-staff",
        "full_name": "New Staff",
        "email": "new-staff@example.com",
        "mobile_phone": "9876543210",
        "role": "office_staff",
        "custom_staff_type": "",
    }, follow_redirects=True)

    user = store.get_user_by_username("new-staff")
    assert response.status_code == 200
    assert user["must_change_password"] is True
    assert not check_password_hash(user["password_hash"], "password")
    assert user["password_reset_token_hash"]
    assert len(sent) == 1
    assert sent[0][0][0] == "new-staff@example.com"
    assert "one-time password setup link" in response.get_data(as_text=True).lower()


@pytest.mark.parametrize("must_change_password", [True, False])
def test_legacy_shared_temporary_password_cannot_authenticate_and_reset_remains_available(
    client, monkeypatch, must_change_password
):
    store = app.config["FIRESTORE_STORE"]
    user = {
        "id": "legacy-user",
        "username": "Legacy",
        "full_name": "Legacy User",
        "email": "legacy@example.com",
        "role": "office_staff",
        "password_hash": generate_password_hash("password"),
        "must_change_password": must_change_password,
        "is_active": True,
        "active_sessions": [],
    }
    store.save_user(user)
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: pytest.fail("Login must not send reset emails."))

    response = client.post(
        "/login",
        data={"username": "Legacy", "password": "password"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert user["must_change_password"] is must_change_password
    assert check_password_hash(user["password_hash"], "password")
    assert not user.get("password_reset_token_hash")
    assert user["active_sessions"] == []
    assert "legacy temporary password can no longer be used" in response.get_data(as_text=True).lower()
    assert "Forgot Password" in response.get_data(as_text=True)


def _save_login_test_user(store, username, password, is_active=True):
    store.save_user({
        "id": username.lower(),
        "username": username,
        "full_name": username,
        "email": f"{username.lower()}@example.com",
        "role": "office_staff",
        "password_hash": generate_password_hash(password),
        "must_change_password": False,
        "is_active": is_active,
        "active_sessions": [],
    })


def test_get_user_store_falls_back_to_memory_when_local_dev_has_no_firebase_config(monkeypatch):
    monkeypatch.delenv("ALLOW_IN_MEMORY_FIRESTORE", raising=False)
    monkeypatch.delenv("FIREBASE_WEB_CONFIG", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_BASE64", raising=False)
    app.config.pop("FIRESTORE_MODE", None)
    app.config.pop("FIRESTORE_STORE", None)
    if hasattr(app, "_memory_store"):
        delattr(app, "_memory_store")
    if hasattr(app, "_firebase_store"):
        delattr(app, "_firebase_store")
    monkeypatch.setattr(app_module, "find_firebase_service_account_path", lambda: None)

    store = app_module.get_user_store()

    assert isinstance(store, app_module.InMemoryFirestoreStore)


def test_firestore_mode_is_read_from_environment_when_app_config_is_unset(monkeypatch):
    monkeypatch.delitem(app.config, "FIRESTORE_MODE", raising=False)
    monkeypatch.setenv("FIRESTORE_MODE", " Firestore ")

    assert app_module._configured_firestore_mode() == "firestore"


def test_packaging_is_declared_as_a_runtime_dependency():
    requirements_path = Path(app_module.__file__).resolve().with_name("requirements.txt")
    requirements = requirements_path.read_text(encoding="utf-8").splitlines()

    assert any(
        requirement.strip().lower().startswith("packaging")
        for requirement in requirements
    )


def test_missing_packaging_in_optional_transport_keeps_firebase_admin_available(
    monkeypatch,
):
    class ExistingFirebaseApp:
        project_id = "test-project"

    class FirebaseAdmin:
        @staticmethod
        def get_app():
            return ExistingFirebaseApp()

    monkeypatch.setattr(app_module, "firebase_admin", FirebaseAdmin)
    monkeypatch.setattr(
        app_module,
        "credentials",
        SimpleNamespace(ApplicationDefault=lambda: object()),
    )
    monkeypatch.setattr(app_module, "_Urllib3FirestoreClient", None)
    monkeypatch.setattr(
        app_module,
        "_FIRESTORE_TRANSPORT_IMPORT_ERROR",
        ModuleNotFoundError("No module named 'packaging'", name="packaging"),
    )
    monkeypatch.setenv("FIRESTORE_GRPC_AUTH_TRANSPORT", "urllib3")
    store = object.__new__(FirebaseFirestoreStore)
    store.project_id = "test-project"
    store.service_account_path = None

    with pytest.raises(
        RuntimeError,
        match="urllib3 Firestore authentication transport is unavailable",
    ) as raised:
        store._initialize_client()

    assert isinstance(raised.value.__cause__, ModuleNotFoundError)
    assert raised.value.__cause__.name == "packaging"
    assert app_module._safe_firebase_error_summary(raised.value.__cause__) == (
        "ModuleNotFoundError",
        "module import failed: packaging",
    )


def test_production_missing_firebase_admin_fails_closed_with_safe_diagnostics(
    monkeypatch, caplog, tmp_path
):
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setattr(app_module, "firebase_admin", None)
    monkeypatch.setattr(app_module, "ACTIVE_FIREBASE_CONFIG_PATH", tmp_path / "missing-active-config.json")
    monkeypatch.setattr(app_module, "find_firebase_service_account_path", lambda: None)
    monkeypatch.setitem(app.config, "FIRESTORE_MODE", "memory")
    monkeypatch.setenv("ALLOW_IN_MEMORY_FIRESTORE", "true")
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("FIREBASE_WEB_CONFIG", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_BASE64", raising=False)
    monkeypatch.delattr(app, "_memory_store", raising=False)
    monkeypatch.delattr(app, "_firebase_store", raising=False)
    monkeypatch.delitem(app.config, "FIRESTORE_STORE", raising=False)
    caplog.set_level("ERROR", logger="app")

    with pytest.raises(RuntimeError, match="Firebase Admin SDK is not installed"):
        app_module.get_user_store()

    assert not hasattr(app, "_memory_store")
    assert "stage=firebase_admin_import" in caplog.text
    assert "project_id=unset" in caplog.text
    assert "web_config_configured=False" in caplog.text
    assert "service_account_json_configured=False" in caplog.text
    assert "service_account_base64_configured=False" in caplog.text


def test_production_login_logs_sanitized_firestore_initialization_cause(
    monkeypatch, caplog, tmp_path
):
    sensitive_marker = "synthetic-private-key-marker"
    credential_path = tmp_path / "service-account.json"
    credential_path.touch()

    class FakeFirebaseAdmin:
        initialize_names = []

        @staticmethod
        def get_app():
            raise ValueError("no default app")

        @classmethod
        def initialize_app(cls, _credentials, options, name=None):
            cls.initialize_names.append(name)
            return SimpleNamespace(
                project_id=options["projectId"],
                credential=SimpleNamespace(get_credential=lambda: object()),
            )

    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setattr(app_module, "firebase_admin", FakeFirebaseAdmin)
    monkeypatch.setattr(
        app_module,
        "credentials",
        SimpleNamespace(Certificate=lambda _path: object()),
    )

    def fail_firestore_client(**_kwargs):
        cause = ValueError(f"access_token={sensitive_marker}")
        raise RuntimeError(f"private_key={sensitive_marker}") from cause

    monkeypatch.setattr(
        app_module,
        "firestore",
        SimpleNamespace(client=fail_firestore_client),
    )
    monkeypatch.setattr(
        app_module,
        "_validate_firebase_service_account_path",
        lambda _path, _project_id: str(credential_path),
    )
    monkeypatch.setattr(app_module, "ACTIVE_FIREBASE_CONFIG_PATH", tmp_path / "missing-active-config.json")
    monkeypatch.setenv("FIRESTORE_MODE", "firestore")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", str(credential_path))
    monkeypatch.delenv("FIRESTORE_GRPC_AUTH_TRANSPORT", raising=False)
    app.config.pop("FIRESTORE_STORE", None)
    monkeypatch.delattr(app, "_firebase_store", raising=False)
    monkeypatch.delattr(app, "_firebase_store_key", raising=False)
    monkeypatch.setattr(
        app_module,
        "_load_active_firebase_config",
        lambda: {
            "project_id": "test-project",
            "service_account_path": str(credential_path),
        },
    )
    caplog.set_level("INFO", logger="app")

    with app.test_client() as client:
        response = client.post(
            "/login",
            data={"username": "private-test-user", "password": "private-test-password"},
            follow_redirects=False,
        )

    assert response.status_code == 503
    assert b"Sign-in is temporarily unavailable" in response.data
    assert "operation=Load Firebase project configuration and validate configured credentials event=complete" in caplog.text
    assert "operation=Validate service-account file before Firebase Admin setup event=complete" in caplog.text
    assert "operation=Initialize default Firebase Admin app event=complete" in caplog.text
    assert "operation=Create Firestore client event=failed" in caplog.text
    assert "exception_type=RuntimeError category=details redacted" in caplog.text
    assert "cause_type=ValueError cause_category=details redacted" in caplog.text
    assert sensitive_marker not in caplog.text
    assert "private-test-user" not in caplog.text
    assert "private-test-password" not in caplog.text
    assert FakeFirebaseAdmin.initialize_names == [None]
    _assert_sequential_login_diagnostics(caplog.text)


def test_production_login_fails_closed_when_firebase_admin_is_unavailable(
    monkeypatch, caplog, tmp_path
):
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setattr(app_module, "firebase_admin", None)
    monkeypatch.setattr(
        app_module,
        "_FIREBASE_ADMIN_IMPORT_ERROR",
        ImportError("synthetic-hidden-import-detail", name="firebase_admin.auth"),
    )
    monkeypatch.setattr(app_module, "ACTIVE_FIREBASE_CONFIG_PATH", tmp_path / "missing-active-config.json")
    monkeypatch.setitem(app.config, "FIRESTORE_MODE", "memory")
    monkeypatch.setenv("ALLOW_IN_MEMORY_FIRESTORE", "true")
    app.config.pop("FIRESTORE_STORE", None)
    monkeypatch.delattr(app, "_memory_store", raising=False)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("FIREBASE_WEB_CONFIG", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_BASE64", raising=False)
    caplog.set_level("INFO", logger="app")

    with app.test_client() as client:
        response = client.post(
            "/login",
            data={"username": "private-test-user", "password": "private-test-password"},
            follow_redirects=False,
        )

    assert response.status_code == 503
    assert b"Sign-in is temporarily unavailable" in response.data
    assert not hasattr(app, "_memory_store")
    assert "operation=Load Firebase Admin SDK event=failed" in caplog.text
    assert "cause_type=ImportError cause_category=module import failed: firebase_admin.auth" in caplog.text
    assert "operation=Resolve user store event=failed" in caplog.text
    assert "exception_type=RuntimeError category=Firebase Admin SDK is unavailable" in caplog.text
    assert "private-test-user" not in caplog.text
    assert "private-test-password" not in caplog.text
    assert "synthetic-hidden-import-detail" not in caplog.text
    _assert_sequential_login_diagnostics(caplog.text)


def _assert_sequential_login_diagnostics(log_text):
    start_steps = [
        int(match.group(1))
        for match in re.finditer(
            r"Sign-in diagnostic step=(\d+) operation=.* event=start",
            log_text,
        )
    ]
    assert start_steps == list(range(1, len(start_steps) + 1))
    for step in start_steps:
        assert len(re.findall(
            rf"Sign-in diagnostic step={step:02d} operation=.* event=(?:complete|failed)",
            log_text,
        )) == 1


def test_firebase_store_initialization_logs_sanitized_cause_and_traceback(
    monkeypatch, caplog, tmp_path
):
    sensitive_marker = "synthetic-private-key-marker"
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setattr(app_module, "firebase_admin", object())
    monkeypatch.setattr(app_module, "ACTIVE_FIREBASE_CONFIG_PATH", tmp_path / "missing-active-config.json")
    monkeypatch.setitem(app.config, "FIRESTORE_MODE", "firestore")
    app.config.pop("FIRESTORE_STORE", None)
    monkeypatch.delattr(app, "_firebase_store", raising=False)
    monkeypatch.delattr(app, "_firebase_store_key", raising=False)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("FIREBASE_WEB_CONFIG", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_BASE64", raising=False)

    def fail_config():
        raise RuntimeError(sensitive_marker)

    monkeypatch.setattr(app_module, "get_active_firebase_config", fail_config)
    caplog.set_level("ERROR", logger="app")

    with pytest.raises(RuntimeError, match="Firestore initialization failed") as raised:
        app_module.get_user_store()

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert sensitive_marker in str(raised.value.__cause__)
    assert "stage=active_firebase_config" in caplog.text
    assert "RuntimeError: details redacted" in caplog.text
    assert "in fail_config" in caplog.text
    assert sensitive_marker not in caplog.text


def test_account_specific_password_login_succeeds(client, caplog):
    store = app.config["FIRESTORE_STORE"]
    _save_login_test_user(store, "AccountLogin", "account-specific-test-value")
    caplog.set_level("INFO", logger="app")

    response = client.post(
        "/login",
        data={"username": "AccountLogin", "password": "account-specific-test-value"},
    )

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["user_id"] == "accountlogin"
    assert "operation=Look up account in user store event=complete" in caplog.text
    assert "operation=Verify submitted password against account hash event=complete" in caplog.text
    assert "operation=Create authenticated session event=complete" in caplog.text
    assert "AccountLogin" not in caplog.text
    assert "account-specific-test-value" not in caplog.text
    _assert_sequential_login_diagnostics(caplog.text)


def test_incorrect_account_password_is_rejected(client):
    store = app.config["FIRESTORE_STORE"]
    _save_login_test_user(store, "WrongPassword", "account-specific-test-value")

    response = client.post(
        "/login",
        data={"username": "WrongPassword", "password": "incorrect-test-value"},
    )

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_login_firestore_timeout_returns_temporary_error_without_logging_credentials(client, monkeypatch, caplog):
    store = app.config["FIRESTORE_STORE"]

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
    assert "Sign-in diagnostic step=01 operation=Read and normalize submitted sign-in fields event=start" in caplog.text
    assert "Firestore request deadline exceeded" in caplog.text
    assert "private-test-user" not in caplog.text
    assert "private-test-password" not in caplog.text


def test_firestore_login_lookup_uses_a_fifteen_second_retry_deadline():
    class FakeQuery:
        def where(self, *, filter):
            assert (filter.field_path, filter.op_string) == ("username_lower", "==")
            return self

        def limit(self, count):
            assert count == 1
            return self

        def stream(self, *, retry, timeout):
            assert retry.timeout == 15
            assert timeout == 15
            return iter(())

    class FakeCollection:
        def where(self, **_kwargs):
            return FakeQuery()

    class FakeClient:
        def collection(self, name):
            assert name == "users"
            return FakeCollection()

    store = FirebaseFirestoreStore(client=FakeClient())

    assert store.get_user_by_username("nobody") is None


def test_inactive_user_cannot_log_in_with_correct_password(client):
    store = app.config["FIRESTORE_STORE"]
    _save_login_test_user(store, "InactiveLogin", "account-specific-test-value", is_active=False)

    response = client.post(
        "/login",
        data={"username": "InactiveLogin", "password": "account-specific-test-value"},
    )

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_custom_staff_type_edit_updates_name(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    login_response = client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)
    assert login_response.status_code == 302

    client.post("/user-management/custom-type", data={"type_name": " Driver "}, follow_redirects=False)
    custom_type = app.config["FIRESTORE_STORE"].list_custom_staff_types()[0]

    response = client.post(
        f"/user-management/custom-type/{custom_type['id']}/edit",
        data={"type_name": " Logistics Staff "},
        follow_redirects=False,
    )

    assert response.status_code == 302
    updated_type = app.config["FIRESTORE_STORE"].list_custom_staff_types()[0]
    assert updated_type["name"] == "Logistics Staff"


def test_custom_staff_type_edit_rejects_duplicate_name_ignoring_case_and_spaces(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    login_response = client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)
    assert login_response.status_code == 302

    client.post("/user-management/custom-type", data={"type_name": "Store Team"}, follow_redirects=False)
    client.post("/user-management/custom-type", data={"type_name": "Warehouse Team"}, follow_redirects=False)
    target = app.config["FIRESTORE_STORE"].list_custom_staff_types()[1]

    response = client.post(
        f"/user-management/custom-type/{target['id']}/edit",
        data={"type_name": "  store   team  "},
        follow_redirects=False,
    )

    assert response.status_code == 302
    names = [item["name"] for item in app.config["FIRESTORE_STORE"].list_custom_staff_types()]
    assert "Warehouse Team" in names
    assert "Store Team" in names


def test_custom_staff_type_delete_removes_unassigned_type(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    login_response = client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)
    assert login_response.status_code == 302

    client.post("/user-management/custom-type", data={"type_name": "Cleaner"}, follow_redirects=False)
    custom_type = app.config["FIRESTORE_STORE"].list_custom_staff_types()[0]

    response = client.post(
        f"/user-management/custom-type/{custom_type['id']}/delete",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert app.config["FIRESTORE_STORE"].list_custom_staff_types() == []


def test_custom_staff_type_delete_blocked_when_users_are_assigned(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    login_response = client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)
    assert login_response.status_code == 302

    client.post("/user-management/custom-type", data={"type_name": "Mechanic"}, follow_redirects=False)
    custom_type = app.config["FIRESTORE_STORE"].list_custom_staff_types()[0]

    app.config["FIRESTORE_STORE"].save_user({
        "id": "u-1",
        "username": "mechanic_user",
        "full_name": "Mechanic User",
        "role": "Mechanic",
        "custom_staff_type": "Mechanic",
        "password_hash": "hash",
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "created_at": "now",
        "updated_at": "now",
    })

    response = client.post(
        f"/user-management/custom-type/{custom_type['id']}/delete",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert len(app.config["FIRESTORE_STORE"].list_custom_staff_types()) == 1


def test_dashboard_uses_four_column_grid(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    login_response = client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)
    assert login_response.status_code == 302

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "cards-grid" in html
    assert "repeat(4, minmax(0, 1fr))" in html


def test_super_admin_can_edit_user_details(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)

    target_user = {
        "id": "user-1",
        "username": "Alpha",
        "full_name": "Alpha User",
        "email": "alpha@example.com",
        "recovery_email": "alpha.recovery@example.com",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": "hash-alpha",
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "created_at": "now",
        "updated_at": "now",
        "permissions": ["dashboard"],
    }
    app.config["FIRESTORE_STORE"].save_user(target_user)

    response = client.post(
        "/user-management/user-1/edit",
        data={
            "username": " alpha ",
            "full_name": "Alpha Smith",
            "email": "alpha.smith@example.com",
            "recovery_email": "alpha.recovery+updated@example.com",
            "role": "supervisor",
            "custom_staff_type": "",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    updated = app.config["FIRESTORE_STORE"].get_user("user-1")
    assert updated["full_name"] == "Alpha Smith"
    assert updated["email"] == "alpha.smith@example.com"
    assert updated["recovery_email"] == "alpha.recovery+updated@example.com"
    assert updated["role"] == "supervisor"
    assert updated["username"] == "alpha"


def test_super_admin_can_grant_and_revoke_camera_upload(client):
    store = app.config["FIRESTORE_STORE"]
    store.save_user({
        "id": "admin-user",
        "username": "Admin",
        "full_name": "Test Admin",
        "email": "admin@example.com",
        "recovery_email": "",
        "role": "super_admin",
        "custom_staff_type": "",
        "password_hash": generate_password_hash("Admin-test-password-42!"),
        "must_change_password": False,
        "status": "active",
        "is_active": True,
        "active_sessions": [],
        "permissions": [],
    })
    login_response = client.post(
        "/login",
        data={"username": "Admin", "password": "Admin-test-password-42!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    app.config["FIRESTORE_STORE"].save_user({
        "id": "camera-user",
        "username": "CameraUser",
        "full_name": "Camera User",
        "email": "camera@example.com",
        "recovery_email": "",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": "hash-camera-user",
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "created_at": "now",
        "updated_at": "now",
        "permissions": ["dashboard"],
    })

    details = client.get("/user-management/camera-user")
    assert b'name="camera_upload"' in details.data

    update = {
        "username": "CameraUser",
        "full_name": "Camera User",
        "email": "camera@example.com",
        "recovery_email": "",
        "role": "office_staff",
        "custom_staff_type": "",
        "is_active": "true",
    }
    response = client.post(
        "/user-management/camera-user/edit",
        data={**update, "camera_upload": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "camera_upload" in app.config["FIRESTORE_STORE"].get_user("camera-user")["permissions"]
    granted_details = client.get("/user-management/camera-user")
    assert b'name="camera_upload" type="checkbox" value="true" checked>' in granted_details.data

    response = client.post(
        "/user-management/camera-user/edit",
        data=update,
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "camera_upload" not in app.config["FIRESTORE_STORE"].get_user("camera-user")["permissions"]


def test_user_edit_rejects_duplicate_username(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)

    app.config["FIRESTORE_STORE"].save_user({
        "id": "u-1",
        "username": "Alpha",
        "full_name": "Alpha User",
        "email": "alpha@example.com",
        "recovery_email": "",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": "hash-alpha",
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "created_at": "now",
        "updated_at": "now",
        "permissions": ["dashboard"],
    })
    app.config["FIRESTORE_STORE"].save_user({
        "id": "u-2",
        "username": "Bravo",
        "full_name": "Bravo User",
        "email": "bravo@example.com",
        "recovery_email": "",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": "hash-bravo",
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "created_at": "now",
        "updated_at": "now",
        "permissions": ["dashboard"],
    })

    response = client.post(
        "/user-management/u-2/edit",
        data={
            "username": " alpha ",
            "full_name": "Bravo User",
            "email": "bravo@example.com",
            "recovery_email": "",
            "role": "office_staff",
            "custom_staff_type": "",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert app.config["FIRESTORE_STORE"].get_user("u-2")["username"] == "Bravo"


def test_user_edit_rejects_invalid_role_and_custom_type(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)

    app.config["FIRESTORE_STORE"].save_user({
        "id": "u-4",
        "username": "Delta",
        "full_name": "Delta User",
        "email": "delta@example.com",
        "recovery_email": "",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": "hash-delta",
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "created_at": "now",
        "updated_at": "now",
        "permissions": ["dashboard"],
    })

    response = client.post(
        "/user-management/u-4/edit",
        data={
            "username": "Delta",
            "full_name": "Delta User",
            "email": "delta@example.com",
            "recovery_email": "",
            "role": "not_a_real_role",
            "custom_staff_type": "Unknown Type",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert app.config["FIRESTORE_STORE"].get_user("u-4")["role"] == "office_staff"


def test_ordinary_user_cannot_edit_user_details(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    app.config["FIRESTORE_STORE"].save_user({
        "id": "u-3",
        "username": "Charlie",
        "full_name": "Charlie User",
        "email": "charlie@example.com",
        "recovery_email": "",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": "hash-charlie",
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "created_at": "now",
        "updated_at": "now",
        "permissions": ["dashboard"],
    })

    login_response = client.post("/login", data={"username": "Charlie", "password": "password"}, follow_redirects=False)
    assert login_response.status_code == 302

    response = client.post(
        "/user-management/u-3/edit",
        data={
            "username": "Charlie",
            "full_name": "Charlie Updated",
            "email": "charlie@updated.com",
            "recovery_email": "",
            "role": "office_staff",
            "custom_staff_type": "",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert app.config["FIRESTORE_STORE"].get_user("u-3")["full_name"] == "Charlie User"


def test_super_admin_cannot_remove_own_privileges(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    rahim = app.config["FIRESTORE_STORE"].get_user_by_username("Rahim")
    client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)

    response = client.post(
        f"/user-management/{rahim['id']}/edit",
        data={
            "username": "Rahim",
            "full_name": "Rahim",
            "email": "rahim@example.com",
            "recovery_email": "",
            "role": "office_staff",
            "custom_staff_type": "",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert app.config["FIRESTORE_STORE"].get_user(rahim["id"])["role"] == "super_admin"


def test_user_edit_generates_audit_log_and_preserves_password(client):
    from app import ensure_initial_super_admin

    ensure_initial_super_admin()
    client.post("/login", data={"username": "Rahim", "password": BOOTSTRAP_TEST_PASSWORD}, follow_redirects=False)

    target = {
        "id": "audit-user",
        "username": "Echo",
        "full_name": "Echo User",
        "email": "echo@example.com",
        "recovery_email": "echo.recovery@example.com",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": "hash-echo",
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "created_at": "now",
        "updated_at": "now",
        "permissions": ["dashboard"],
    }
    app.config["FIRESTORE_STORE"].save_user(target)

    response = client.post(
        "/user-management/audit-user/edit",
        data={
            "username": "echo",
            "full_name": "Echo Updated",
            "email": "echo.updated@example.com",
            "recovery_email": "echo.recovery+new@example.com",
            "role": "office_staff",
            "custom_staff_type": "",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert app.config["FIRESTORE_STORE"].get_user("audit-user")["password_hash"] == "hash-echo"
    log = app.config["FIRESTORE_STORE"].audit_log[-1]
    assert log["action"] == "user_updated"
    assert "full_name" in log["details"]


def test_user_management_requires_super_admin(client):
    response = client.get("/user-management", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"


def test_user_management_renders_saved_avatar_id_next_to_full_name(client):
    store = app.config["FIRESTORE_STORE"]
    store.save_user({
        "id": "admin-user",
        "username": "admin",
        "full_name": "Admin User",
        "role": "super_admin",
        "is_active": True,
        "active_sessions": ["test-session"],
        "avatarId": "panda",
    })
    store.save_user({
        "id": "staff-user",
        "username": "staff",
        "full_name": "Staff User",
        "role": "office_staff",
        "is_active": True,
        "avatarId": "fox",
    })
    with client.session_transaction() as sess:
        sess["user_id"] = "admin-user"
        sess["session_id"] = "test-session"
        sess["role"] = "super_admin"

    response = client.get("/user-management")

    assert response.status_code == 200
    assert b'data-animal-avatar="fox"' in response.data
    assert b'data-animal-avatar="panda"' in response.data
    assert b'<span>Staff User</span>' in response.data
    assert "–" in response.get_data(as_text=True)
    assert "<td>–</td>" in response.get_data(as_text=True)
    assert "\u00e2\u20ac\u201c" not in response.get_data(as_text=True)
    html = response.get_data(as_text=True)
    assert 'getAnimalAvatar, renderAnimalAvatar' in html
    for asset in (
        "dialogs.css",
        "dialogs.js",
        "design_system.css",
        "site_motion.css",
    ):
        assert f"/static/{asset}?v=" in html
    assert "office_connection.css" not in html
    assert "office_connection.js" not in html
    assert "OFFICE WI-FI DISCONNECTED" not in html
    assert "You appear to be outside the range of the Mannat Moon office Wi-Fi" not in html
    assert "Office connection restored." not in html
    assert "website-dark-surface-20261002-1" not in html


def test_shared_confirmation_dialog_uses_green_theme():
    dialog_css = (
        Path(__file__).resolve().parents[1] / "static" / "dialogs.css"
    ).read_text(encoding="utf-8")

    assert "background: #111b17;" in dialog_css
    assert "border: 1px solid #263c31;" in dialog_css
    assert "background: #a9e4bc;" in dialog_css
    assert "outline: 3px solid #a9e4bc;" in dialog_css
    assert "background: rgba(4, 8, 15, .72);" in dialog_css


def test_daily_report_routes_require_authentication_and_do_not_return_report_data(client, monkeypatch):
    store = app.config["FIRESTORE_STORE"]
    store.daily_records = {
        "daily-record-1": {
            "date": "2026-10-03",
            "activity": "Private report data",
        }
    }
    monkeypatch.setattr(
        "app._get_daily_record_for_date",
        lambda _date: pytest.fail("Unauthenticated requests must not read Daily Reports."),
    )

    page_response = client.get(
        "/daily-reports?date=2026-10-03",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        follow_redirects=False,
    )
    assert page_response.status_code == 302
    assert page_response.headers["Location"].startswith("/login?next=")
    login_response = client.get(page_response.headers["Location"])
    assert login_response.status_code == 200
    assert b"Please sign in to continue" in login_response.data
    assert b"Private report data" not in login_response.data

    image_response = client.get(
        "/daily-reports/share-image?date=2026-10-03",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert image_response.status_code == 401
    assert "Private report data" not in image_response.get_data(as_text=True)
    assert "<svg" not in image_response.get_data(as_text=True)


def test_daily_reports_login_returns_to_requested_date_and_rejects_external_next(client):
    from app import ensure_initial_super_admin

    admin = ensure_initial_super_admin()
    admin["must_change_password"] = False
    app.config["FIRESTORE_STORE"].save_user(admin)
    requested_page = client.get("/daily-reports?date=2026-10-03", follow_redirects=False)
    login_page = client.get(requested_page.headers["Location"])
    assert b'name="next" value="/daily-reports?date=2026-10-03"' in login_page.data

    login_response = client.post(
        "/login",
        data={
            "username": "Rahim",
            "password": BOOTSTRAP_TEST_PASSWORD,
            "next": "/daily-reports?date=2026-10-03",
        },
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    assert login_response.headers["Location"] == "/daily-reports?date=2026-10-03"

    with client.session_transaction() as flask_session:
        flask_session.clear()
    unsafe_next_response = client.post(
        "/login",
        data={
            "username": "Rahim",
            "password": BOOTSTRAP_TEST_PASSWORD,
            "next": "https://example.com",
        },
        follow_redirects=False,
    )
    assert unsafe_next_response.status_code == 302
    assert unsafe_next_response.headers["Location"] == "/"


def test_daily_report_routes_require_daily_reports_read_permission(client):
    store = app.config["FIRESTORE_STORE"]
    store.save_user({
        "id": "report-user",
        "username": "report-user",
        "role": "Report Viewer",
        "custom_staff_type": "Report Viewer",
        "status": "active",
        "is_active": True,
        "active_sessions": ["report-session"],
    })
    store.save_custom_staff_type({
        "id": "report-viewer",
        "name": "Report Viewer",
        "permissions": [],
    })
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "report-user"
        flask_session["session_id"] = "report-session"

    for path in ("/daily-reports", "/daily-reports/share-image"):
        response = client.get(path)
        assert response.status_code == 403


def test_authenticated_daily_report_reader_can_open_page_and_share_image(client, monkeypatch):
    store = app.config["FIRESTORE_STORE"]
    store.save_user({
        "id": "report-user",
        "username": "report-user",
        "role": "super_admin",
        "status": "active",
        "is_active": True,
        "active_sessions": ["report-session"],
    })
    monkeypatch.setattr(
        "app._get_daily_record_for_date",
        lambda _date: {"date": "2026-10-03", "activity": "Authorized report data"},
    )
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "report-user"
        flask_session["session_id"] = "report-session"

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


def test_anonymous_users_cannot_mutate_daily_reports(client):
    response = client.post(
        "/api/daily-records",
        json={
            "date": "2026-10-03",
            "activity": "Concrete pour",
            "floor": "Ground Floor",
            "tradeName": "Masonry",
            "weatherCondition": "Sunny",
            "materials": "Cement bags",
            "notes": "Created anonymously",
        },
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "Authentication required."
