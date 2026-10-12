import pytest

from app import InMemoryFirestoreStore, app


@pytest.fixture
def admin_client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.save_user({
        "id": "csrf-admin",
        "username": "csrf-admin",
        "full_name": "CSRF Admin",
        "email": "admin@example.test",
        "role": "super_admin",
        "is_active": True,
        "status": "active",
        "active_sessions": ["csrf-session"],
        "permissions": [],
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "authenticated-form-csrf-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "https://app.example.test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "csrf-admin"
        session["session_id"] = "csrf-session"
        session["role"] = "super_admin"
    return client, store


def _csrf_token_from_session(client):
    with client.session_transaction() as session:
        return session["authenticated_form_csrf"]


def _create_user(client, *, token=None):
    payload = {
        "username": "new-account",
        "full_name": "New Account",
        "email": "new-account@example.test",
        "role": "office_staff",
        "custom_staff_type": "",
    }
    if token is not None:
        payload["csrf_token"] = token
    return client.post("/user-management/create", data=payload, follow_redirects=False)


def test_authenticated_html_form_rejects_missing_csrf(admin_client):
    client, store = admin_client

    response = _create_user(client)

    assert response.status_code == 400
    assert store.get_user_by_username("new-account") is None


def test_authenticated_html_post_routes_use_the_csrf_guard():
    endpoints = (
        "test_email",
        "logout",
        "change_password",
        "forgot_password",
        "google_photos",
        "user_management_create",
        "custom_staff_type_create",
        "custom_staff_type_edit",
        "custom_staff_type_delete",
        "user_management_edit",
        "user_management_toggle_status",
        "user_management_reset_password",
    )

    assert all(
        getattr(app.view_functions[endpoint], "_requires_authenticated_form_csrf", False)
        for endpoint in endpoints
    )


def test_authenticated_html_form_rejects_invalid_csrf(admin_client):
    client, store = admin_client
    client.get("/user-management")

    response = _create_user(client, token="invalid-token")

    assert response.status_code == 400
    assert store.get_user_by_username("new-account") is None


def test_authenticated_html_form_accepts_session_bound_csrf(admin_client, monkeypatch):
    client, store = admin_client
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: True)
    client.get("/user-management")
    token = _csrf_token_from_session(client)

    response = _create_user(client, token=token)

    assert response.status_code == 302
    assert store.get_user_by_username("new-account") is not None


def test_authenticated_html_form_rejects_token_bound_to_previous_session(admin_client, monkeypatch):
    client, store = admin_client
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: True)
    client.get("/user-management")
    old_token = _csrf_token_from_session(client)
    admin = store.get_user("csrf-admin")
    admin["active_sessions"] = ["replacement-session"]
    store.save_user(admin)
    with client.session_transaction() as session:
        session["session_id"] = "replacement-session"

    response = _create_user(client, token=old_token)

    assert response.status_code == 400
    assert store.get_user_by_username("new-account") is None
