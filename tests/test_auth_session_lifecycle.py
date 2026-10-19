from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from app import InMemoryFirestoreStore, app


PASSWORD = "CurrentSecure1!"


def save_user(store, user_id, role, *, active=True, sessions=None, must_change=False):
    return store.save_user({
        "id": user_id,
        "username": user_id,
        "full_name": user_id,
        "email": f"{user_id}@example.invalid",
        "recovery_email": "",
        "role": role,
        "custom_staff_type": "",
        "password_hash": generate_password_hash(PASSWORD),
        "must_change_password": must_change,
        "status": "active" if active else "inactive",
        "is_active": active,
        "active_sessions": list(sessions or []),
        "permissions": ["camera_upload"] if role == "office_staff" else [],
        "authorization_version": 3,
    })


@pytest.fixture
def auth_clients(monkeypatch):
    store = InMemoryFirestoreStore()
    save_user(store, "admin-user", "super_admin", sessions=["admin-session"])
    save_user(store, "staff-user", "office_staff", sessions=["staff-session", "staff-other"])
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "session-lifecycle-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)

    admin_client = app.test_client()
    with admin_client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin-user"
        flask_session["role"] = "super_admin"
        flask_session["session_id"] = "admin-session"

    staff_client = app.test_client()
    with staff_client.session_transaction() as flask_session:
        flask_session["user_id"] = "staff-user"
        flask_session["role"] = "office_staff"
        flask_session["session_id"] = "staff-session"

    for client in (admin_client, staff_client):
        original_post = client.post

        def post_with_authenticated_form_csrf(*args, _client=client, _post=original_post, **kwargs):
            path = str(args[0]) if args else str(kwargs.get("path", ""))
            protected_paths = (
                "/logout",
                "/test-email",
                "/change-password",
                "/forgot-password",
                "/user-management/",
            )
            if path.startswith(protected_paths):
                _client.get("/profile-settings")
                with _client.session_transaction() as flask_session:
                    csrf_token = flask_session.get("authenticated_form_csrf", "")
                form_data = kwargs.get("data")
                if isinstance(form_data, dict):
                    kwargs["data"] = {**form_data, "csrf_token": csrf_token}
                else:
                    kwargs["data"] = {"csrf_token": csrf_token}
            return _post(*args, **kwargs)

        monkeypatch.setattr(client, "post", post_with_authenticated_form_csrf)

    return store, admin_client, staff_client


def edit_user(client, user_id, *, role="office_staff", active="true"):
    return client.post(
        f"/user-management/{user_id}/edit",
        data={
            "username": user_id,
            "full_name": user_id,
            "email": f"{user_id}@example.invalid",
            "recovery_email": "",
            "role": role,
            "custom_staff_type": "",
            "is_active": active,
            "camera_upload": "true",
        },
        follow_redirects=False,
    )


def test_active_user_session_continues_to_work(auth_clients):
    _store, _admin_client, staff_client = auth_clients

    response = staff_client.get("/")

    assert response.status_code == 200


def test_inactive_user_session_is_rejected_and_invalidated(auth_clients):
    store, _admin_client, staff_client = auth_clients
    user = store.get_user("staff-user")
    user["status"] = "inactive"
    user["is_active"] = False
    store.save_user(user)

    response = staff_client.get("/")

    assert response.status_code == 401
    assert b"You need to sign in to view this page." in response.data
    assert store.get_user("staff-user")["active_sessions"] == []
    with staff_client.session_transaction() as flask_session:
        assert not flask_session


def test_deactivation_invalidates_sessions_and_reactivation_requires_login(auth_clients):
    store, admin_client, staff_client = auth_clients

    deactivated = admin_client.post(
        "/user-management/staff-user/toggle-status",
        follow_redirects=False,
    )
    assert deactivated.status_code == 302
    inactive_user = store.get_user("staff-user")
    assert inactive_user["is_active"] is False
    assert inactive_user["active_sessions"] == []
    assert inactive_user["authorization_version"] == 4
    assert staff_client.get("/").status_code == 401

    reactivated = admin_client.post(
        "/user-management/staff-user/toggle-status",
        follow_redirects=False,
    )
    assert reactivated.status_code == 302
    active_user = store.get_user("staff-user")
    assert active_user["is_active"] is True
    assert active_user["active_sessions"] == []
    assert active_user["authorization_version"] == 5
    assert staff_client.get("/").status_code == 401

    fresh_client = app.test_client()
    login_response = fresh_client.post(
        "/login",
        data={"username": "staff-user", "password": PASSWORD},
        follow_redirects=False,
    )
    assert login_response.headers["Location"].endswith("/")
    assert fresh_client.get("/").status_code == 200


def test_logout_invalidates_only_the_current_session(auth_clients):
    store, _admin_client, staff_client = auth_clients

    response = staff_client.post("/logout", follow_redirects=False)

    assert response.status_code == 302
    assert store.get_user("staff-user")["active_sessions"] == ["staff-other"]
    assert store.get_user("admin-user")["active_sessions"] == ["admin-session"]


def test_role_change_increments_version_and_invalidates_old_sessions(auth_clients):
    store, admin_client, _staff_client = auth_clients

    response = edit_user(admin_client, "staff-user", role="supervisor")

    assert response.status_code == 302
    updated = store.get_user("staff-user")
    assert updated["role"] == "supervisor"
    assert updated["authorization_version"] == 4
    assert updated["active_sessions"] == []


def test_permission_change_invalidates_sessions_without_changing_camera_permission_policy(auth_clients):
    store, admin_client, _staff_client = auth_clients
    user = store.get_user("staff-user")
    user["permissions"] = []
    user["active_sessions"] = ["staff-session"]
    store.save_user(user)

    response = edit_user(admin_client, "staff-user")

    assert response.status_code == 302
    updated = store.get_user("staff-user")
    assert "camera_upload" in updated["permissions"]
    assert updated["authorization_version"] == 4
    assert updated["active_sessions"] == []


def test_custom_role_rename_invalidates_assigned_sessions(auth_clients):
    store, admin_client, _staff_client = auth_clients
    store.save_custom_staff_type({"id": "custom-1", "name": "Site Engineer"})
    user = store.get_user("staff-user")
    user["role"] = "Site Engineer"
    user["custom_staff_type"] = "Site Engineer"
    user["active_sessions"] = ["staff-session"]
    store.save_user(user)

    response = admin_client.post(
        "/user-management/custom-type/custom-1/edit",
        data={"type_name": "Senior Site Engineer"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    updated = store.get_user("staff-user")
    assert updated["active_sessions"] == []
    assert updated["authorization_version"] == 4


def test_must_change_password_login_still_requires_password_change(monkeypatch):
    store = InMemoryFirestoreStore()
    save_user(store, "new-staff", "office_staff", sessions=[], must_change=True)
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "session-lifecycle-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()

    login_response = client.post(
        "/login",
        data={"username": "new-staff", "password": PASSWORD},
        follow_redirects=False,
    )

    assert login_response.headers["Location"].endswith("/change-password")
    assert store.get_user("new-staff")["must_change_password"] is True
    assert client.get("/change-password").status_code == 200
    with client.session_transaction() as flask_session:
        csrf_token = flask_session["authenticated_form_csrf"]
    changed = client.post(
        "/change-password",
        data={
            "current_password": PASSWORD,
            "new_password": "ReplacementSecure2!",
            "confirm_new_password": "ReplacementSecure2!",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )
    assert changed.headers["Location"].endswith("/")
    updated = store.get_user("new-staff")
    assert updated["must_change_password"] is False
    assert updated["authorization_version"] == 4
    assert len(updated["active_sessions"]) == 1
    assert client.get("/").status_code == 200


def test_super_admin_recovery_invalidates_sessions_and_advances_version(auth_clients):
    store, _admin_client, _staff_client = auth_clients
    user = store.get_user("admin-user")
    user["recovery_email"] = "admin-user@example.invalid"
    user["recovery_code_hash"] = generate_password_hash("RECOVERY-CODE")
    user["recovery_code_used"] = False
    store.save_user(user)
    client = app.test_client()

    response = client.post(
        "/super-admin-recovery",
        data={
            "username": "admin-user",
            "recovery_email": "admin-user@example.invalid",
            "recovery_code": "RECOVERY-CODE",
            "new_password": "AdminReplacement2!",
            "confirm_password": "AdminReplacement2!",
        },
        follow_redirects=False,
    )

    assert response.headers["Location"].endswith("/login")
    updated = store.get_user("admin-user")
    assert updated["active_sessions"] == []
    assert updated["authorization_version"] == 4


def test_incomplete_or_mismatched_user_record_fails_closed(auth_clients):
    store, _admin_client, staff_client = auth_clients
    store.users["staff-user"] = {
        "id": "different-user",
        "role": "office_staff",
        "status": "active",
        "active_sessions": ["staff-session"],
    }

    response = staff_client.get("/")

    assert response.status_code == 401
    assert b"You need to sign in to view this page." in response.data


def test_password_reset_invalidates_sessions_and_advances_authorization_version(auth_clients):
    store, _admin_client, _staff_client = auth_clients
    user = store.get_user("staff-user")
    token_hash = "reset-token-hash"
    user["password_reset_token_hash"] = token_hash
    user["password_reset_expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    store.save_user(user)

    consumed = store.consume_password_reset(
        "staff-user",
        token_hash,
        generate_password_hash("ResetSecure2!"),
        datetime.now(timezone.utc),
    )

    updated = store.get_user("staff-user")
    assert consumed is True
    assert updated["active_sessions"] == []
    assert updated["authorization_version"] == 4
