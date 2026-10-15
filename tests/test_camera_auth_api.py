import time

import pytest
from itsdangerous import SignatureExpired
from werkzeug.security import generate_password_hash

import app as app_module
from app import InMemoryFirestoreStore, app, verify_camera_auth_assertion


API_SECRET = "test-camera-api-secret-with-at-least-32-bytes"
ASSERTION_SECRET = "test-camera-assertion-secret-with-32-bytes-minimum"


@pytest.fixture
def client(monkeypatch):
    store = InMemoryFirestoreStore()
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "camera-auth-test-flask-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    monkeypatch.setitem(app.config, "CAMERA_AUTH_API_SECRET", API_SECRET)
    monkeypatch.setitem(app.config, "CAMERA_AUTH_ASSERTION_SECRET", ASSERTION_SECRET)
    with app.test_client() as test_client:
        yield test_client, store


def add_user(store, *, user_id="user-1", username="camera-user", status="active", role="office_staff", permissions=None):
    store.save_user({
        "id": user_id,
        "username": username,
        "username_lower": username.lower(),
        "full_name": "Camera Test User",
        "email": "camera-test@example.invalid",
        "role": role,
        "custom_staff_type": "",
        "password_hash": generate_password_hash("A-test-password-42!"),
        "must_change_password": False,
        "status": status,
        "is_active": status == "active",
        "active_sessions": [],
        "permissions": list(permissions or []),
    })


def authenticate(client, *, username="camera-user", password="A-test-password-42!", api_secret=API_SECRET, body=None):
    headers = {"Authorization": f"Bearer {api_secret}"} if api_secret is not None else {}
    return client.post(
        "/api/camera/authenticate",
        json=body if body is not None else {"username": username, "password": password},
        headers=headers,
    )


def test_active_operational_user_gets_minimal_identity_and_signed_assertion(client):
    test_client, store = client
    add_user(store, permissions=["camera_upload"])

    response = authenticate(test_client)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["authenticated"] is True
    assert payload["user_id"] == "user-1"
    assert payload["permissions"] == ["camera_upload"]
    assert "password_hash" not in payload
    assert "active_sessions" not in payload
    claims = verify_camera_auth_assertion(payload["assertion"])
    assert claims["aud"] == "camera"
    assert claims["sub"] == "user-1"
    assert claims["permissions"] == ["camera_upload"]
    assert response.headers["Cache-Control"] == "no-store"
    assert store.audit_log[-1]["action"] == "camera_auth_success"


def test_existing_mannat_login_route_still_authenticates_user(client):
    test_client, store = client
    add_user(store)

    response = test_client.post(
        "/login",
        data={"username": "camera-user", "password": "A-test-password-42!"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    with test_client.session_transaction() as flask_session:
        assert flask_session["user_id"] == "user-1"
        assert flask_session.get("session_id")


@pytest.mark.parametrize(
    ("username", "password"),
    [("camera-user", "wrong-password"), ("missing-user", "A-test-password-42!")],
)
def test_wrong_password_and_unknown_user_have_same_generic_failure(client, username, password):
    test_client, store = client
    add_user(store)

    response = authenticate(test_client, username=username, password=password)

    assert response.status_code == 401
    assert response.get_json() == {"authenticated": False, "error": "Camera authentication failed."}
    assert store.audit_log[-1]["action"] == "camera_auth_failed"
    assert password not in store.audit_log[-1]["details"]


def test_inactive_user_is_denied_even_with_valid_password(client):
    test_client, store = client
    add_user(store, status="inactive", permissions=["camera_upload"])

    response = authenticate(test_client)

    assert response.status_code == 401
    assert response.get_json()["error"] == "Camera authentication failed."
    assert store.audit_log[-1]["action"] == "camera_auth_denied"


def test_user_required_to_change_password_is_denied(client):
    test_client, store = client
    add_user(store, permissions=["camera_upload"])
    user = store.get_user("user-1")
    user["must_change_password"] = True
    store.save_user(user)

    response = authenticate(test_client)

    assert response.status_code == 401
    assert response.get_json()["error"] == "Camera authentication failed."
    assert store.audit_log[-1]["action"] == "camera_auth_denied"


def test_active_user_without_camera_permission_is_denied(client):
    test_client, store = client
    add_user(store, permissions=["dashboard"])
    store.save_non_admin_permissions(
        sorted(set(store.get_non_admin_permissions()) - {"camera_upload"})
    )

    response = authenticate(test_client)

    assert response.status_code == 401
    assert response.get_json()["error"] == "Camera authentication failed."
    assert store.audit_log[-1]["action"] == "camera_auth_denied"


def test_missing_server_secret_fails_closed(client, monkeypatch):
    test_client, _store = client
    monkeypatch.setitem(app.config, "CAMERA_AUTH_API_SECRET", "")

    response = authenticate(test_client)

    assert response.status_code == 503
    assert response.get_json()["authenticated"] is False


def test_invalid_server_credential_is_rejected(client):
    test_client, _store = client

    response = authenticate(test_client, api_secret="invalid-camera-api-secret")

    assert response.status_code == 401
    assert response.get_json()["error"] == "Camera authentication failed."


def test_missing_caller_credential_is_rejected(client):
    test_client, _store = client

    response = authenticate(test_client, api_secret=None)

    assert response.status_code == 401
    assert response.get_json()["error"] == "Camera authentication failed."


def test_expired_assertion_is_rejected(client, monkeypatch):
    test_client, store = client
    add_user(store, permissions=["camera_upload"])
    token = authenticate(test_client).get_json()["assertion"]
    original_time = time.time
    monkeypatch.setattr(app_module.time, "time", lambda: original_time() + 600)

    with pytest.raises(SignatureExpired):
        verify_camera_auth_assertion(token)


def test_malformed_request_is_rejected(client):
    test_client, _store = client

    response = test_client.post(
        "/api/camera/authenticate",
        data="not-json",
        content_type="application/json",
        headers={"Authorization": f"Bearer {API_SECRET}"},
    )

    assert response.status_code == 400
    assert response.get_json()["authenticated"] is False


def test_repeated_failed_authentication_is_rate_limited(client):
    test_client, store = client
    add_user(store)

    responses = [authenticate(test_client, password="wrong-password") for _ in range(6)]

    assert [response.status_code for response in responses] == [401, 401, 401, 401, 401, 429]
    assert sum(entry["action"] == "camera_auth_failed" for entry in store.audit_log) == 5


def test_audit_records_camera_auth_failure_without_password(client):
    test_client, store = client
    add_user(store)
    password = "never-write-this-password"

    authenticate(test_client, password=password)

    entry = store.audit_log[-1]
    assert entry["action"] == "camera_auth_failed"
    assert password not in str(entry)
    assert API_SECRET not in str(entry)