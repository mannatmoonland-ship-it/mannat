from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from app import InMemoryFirestoreStore, app


@pytest.fixture
def firebase_token_client(monkeypatch):
    store = InMemoryFirestoreStore()
    firebase_app = object()
    store.firebase_app = firebase_app
    user = store.save_user({
        "id": "stable-user-id-123",
        "username": "token-user",
        "full_name": "Token User",
        "email": "token-user@example.invalid",
        "role": "super_admin",
        "custom_staff_type": "",
        "password_hash": generate_password_hash("CurrentSecure1!"),
        "must_change_password": False,
        "status": "active",
        "is_active": True,
        "active_sessions": ["active-session-id"],
        "authorization_version": 7,
        "permissions": [],
    })
    created_tokens = []

    def create_custom_token(uid, claims, app=None):
        created_tokens.append((uid, dict(claims), app))
        return b"mock-firebase-custom-token"

    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "firebase-token-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    monkeypatch.setattr(
        app_module,
        "firebase_auth",
        SimpleNamespace(create_custom_token=create_custom_token),
    )
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user["id"]
        flask_session["role"] = user["role"]
        flask_session["session_id"] = "active-session-id"

    return store, client, created_tokens, firebase_app


def get_csrf_token(client):
    response = client.get("/api/firebase-custom-token")
    assert response.status_code == 200
    return response.get_json()["csrf_token"]


def post_for_custom_token(client, csrf_token, payload=None, *, origin="http://localhost"):
    return client.post(
        "/api/firebase-custom-token",
        json=payload or {},
        headers={"X-CSRF-Token": csrf_token, "Origin": origin},
    )


@pytest.mark.parametrize(
    ("role", "expected_permissions"),
    [
        ("super_admin", {"trade_builder.write", "contract_values.edit", "user_admin.manage"}),
        ("office_staff", {"labour_payments.create", "daily_reports.read", "ledger.read"}),
        ("supervisor", {"labour_payments.create", "daily_reports.read", "ledger.read"}),
    ],
)
def test_builtin_roles_receive_custom_tokens_with_compiled_permissions(
    firebase_token_client, role, expected_permissions
):
    store, client, created_tokens, firebase_app = firebase_token_client
    user = store.get_user("stable-user-id-123")
    user["role"] = role
    user["active_sessions"] = ["active-session-id"]
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["role"] = role
        flask_session["session_id"] = "active-session-id"

    response = post_for_custom_token(client, get_csrf_token(client))

    assert response.status_code == 200
    assert response.get_json() == {"token": "mock-firebase-custom-token"}
    uid, claims, used_app = created_tokens[-1]
    assert uid == "stable-user-id-123"
    assert claims["role"] == role
    assert expected_permissions.issubset(set(claims["perms"]))
    assert claims["sid"] == "active-session-id"
    assert claims["ver"] == 7
    assert used_app is firebase_app


def test_custom_role_without_configured_permissions_gets_empty_perms(firebase_token_client):
    store, client, created_tokens, _firebase_app = firebase_token_client
    user = store.get_user("stable-user-id-123")
    user.update({
        "role": "Site Engineer",
        "custom_staff_type": "Site Engineer",
        "active_sessions": ["active-session-id"],
    })
    store.save_user(user)
    store.save_custom_staff_type({"id": "custom-role-1", "name": "Site Engineer"})
    with client.session_transaction() as flask_session:
        flask_session["role"] = "Site Engineer"

    response = post_for_custom_token(client, get_csrf_token(client))

    assert response.status_code == 200
    assert created_tokens[-1][1]["perms"] == []


def test_legacy_user_permissions_do_not_grant_custom_role_claims(firebase_token_client):
    store, client, created_tokens, _firebase_app = firebase_token_client
    user = store.get_user("stable-user-id-123")
    user.update({
        "role": "Unconfigured Role",
        "custom_staff_type": "Unconfigured Role",
        "permissions": ["trade_builder.write", "user_admin.manage"],
        "active_sessions": ["active-session-id"],
    })
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["role"] = "Unconfigured Role"

    response = post_for_custom_token(client, get_csrf_token(client))

    assert response.status_code == 200
    assert created_tokens[-1][1]["perms"] == []


def test_custom_role_receives_only_explicit_allowlisted_permissions(firebase_token_client):
    store, client, created_tokens, _firebase_app = firebase_token_client
    user = store.get_user("stable-user-id-123")
    user.update({
        "role": "Site Engineer",
        "custom_staff_type": "Site Engineer",
        "active_sessions": ["active-session-id"],
    })
    store.save_user(user)
    store.save_custom_staff_type({
        "id": "custom-role-1",
        "name": "Site Engineer",
        "permissions": ["materials.read", "daily_reports.create", "not.a.permission"],
    })
    with client.session_transaction() as flask_session:
        flask_session["role"] = "Site Engineer"

    response = post_for_custom_token(client, get_csrf_token(client))

    assert response.status_code == 200
    assert created_tokens[-1][1]["perms"] == ["daily_reports.create", "materials.read"]


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        ("inactive", 401),
        ("revoked_session", 401),
        ("must_change_password", 403),
    ],
)
def test_unavailable_accounts_and_sessions_cannot_request_tokens(
    firebase_token_client, mutation, expected_status
):
    store, client, created_tokens, _firebase_app = firebase_token_client
    csrf_token = get_csrf_token(client)
    user = store.get_user("stable-user-id-123")
    if mutation == "inactive":
        user["status"] = "inactive"
        user["is_active"] = False
    elif mutation == "revoked_session":
        user["active_sessions"] = []
    elif mutation == "must_change_password":
        user["must_change_password"] = True
    store.save_user(user)

    response = post_for_custom_token(client, csrf_token)

    assert response.status_code == expected_status
    assert created_tokens == []


def test_missing_or_incomplete_flask_session_is_rejected(firebase_token_client):
    _store, client, created_tokens, _firebase_app = firebase_token_client
    with client.session_transaction() as flask_session:
        flask_session.clear()

    response = client.post(
        "/api/firebase-custom-token",
        json={"role": "super_admin", "permissions": ["user_admin.manage"], "uid": "forged"},
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 401
    assert created_tokens == []


def test_token_claims_ignore_forged_browser_identity_and_permissions(firebase_token_client):
    _store, client, created_tokens, _firebase_app = firebase_token_client
    csrf_token = get_csrf_token(client)

    response = post_for_custom_token(
        client,
        csrf_token,
        {
            "role": "super_admin",
            "permissions": ["user_admin.manage"],
            "uid": "some-other-user",
            "authorization_version": 999999,
        },
    )

    assert response.status_code == 200
    uid, claims, _used_app = created_tokens[-1]
    assert uid == "stable-user-id-123"
    assert claims == {
        "role": "super_admin",
        "perms": sorted(app_module.compile_effective_permissions({"role": "super_admin", "is_active": True})),
        "sid": "active-session-id",
        "ver": 7,
    }
    assert "uid" not in claims


def test_authorization_version_is_read_from_current_user_record(firebase_token_client):
    store, client, created_tokens, _firebase_app = firebase_token_client
    user = store.get_user("stable-user-id-123")
    user["authorization_version"] = 12
    store.save_user(user)

    response = post_for_custom_token(client, get_csrf_token(client))

    assert response.status_code == 200
    assert created_tokens[-1][1]["ver"] == 12


def test_custom_token_requires_valid_csrf_and_same_origin(firebase_token_client):
    _store, client, created_tokens, _firebase_app = firebase_token_client
    csrf_token = get_csrf_token(client)

    no_origin = client.post(
        "/api/firebase-custom-token",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    forged_origin = post_for_custom_token(client, csrf_token, origin="https://attacker.example")
    forged_csrf = post_for_custom_token(client, "wrong-token")

    assert no_origin.status_code == 400
    assert forged_origin.status_code == 400
    assert forged_csrf.status_code == 400
    assert created_tokens == []


def test_token_response_is_not_cached_or_logged(firebase_token_client, caplog):
    _store, client, _created_tokens, _firebase_app = firebase_token_client
    csrf_response = client.get("/api/firebase-custom-token")
    csrf_token = csrf_response.get_json()["csrf_token"]
    response = post_for_custom_token(client, csrf_token)

    assert csrf_response.headers["Cache-Control"] == "no-store"
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert "mock-firebase-custom-token" not in caplog.text
    assert "mock-firebase-custom-token" not in _store.get_user("stable-user-id-123").values()


def test_firebase_admin_configuration_failure_returns_controlled_error(firebase_token_client):
    store, client, created_tokens, _firebase_app = firebase_token_client
    csrf_token = get_csrf_token(client)
    store.firebase_app = None

    response = post_for_custom_token(client, csrf_token)

    assert response.status_code == 503
    assert response.get_json() == {"error": "Firebase authentication is temporarily unavailable."}
    assert response.headers["Cache-Control"] == "no-store"
    assert created_tokens == []


def test_firebase_admin_token_generation_failure_returns_controlled_error(firebase_token_client, monkeypatch):
    _store, client, _created_tokens, _firebase_app = firebase_token_client

    def fail_token_generation(uid, claims, app=None):
        raise RuntimeError("sensitive token generation detail")

    monkeypatch.setattr(app_module.firebase_auth, "create_custom_token", fail_token_generation)
    response = post_for_custom_token(client, get_csrf_token(client))

    assert response.status_code == 503
    assert response.get_json() == {"error": "Firebase authentication is temporarily unavailable."}


def test_revoked_session_after_role_change_cannot_request_token(firebase_token_client):
    store, client, created_tokens, _firebase_app = firebase_token_client
    csrf_token = get_csrf_token(client)
    user = store.get_user("stable-user-id-123")
    user["role"] = "office_staff"
    user["authorization_version"] = 8
    user["active_sessions"] = []
    store.save_user(user)

    response = post_for_custom_token(client, csrf_token)

    assert response.status_code == 401
    assert created_tokens == []
