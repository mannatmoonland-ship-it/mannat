import io

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from app import InMemoryFirestoreStore, app


SESSION_ID = "daily-drive-test-session"
USER_ID = "daily-drive-test-user"
CSRF = "daily-drive-test-csrf"


@pytest.fixture
def daily_drive_client(monkeypatch):
    store = InMemoryFirestoreStore()
    user = store.save_user({
        "id": USER_ID,
        "username": "daily-drive-user",
        "role": "super_admin",
        "custom_staff_type": "",
        "status": "active",
        "is_active": True,
        "active_sessions": [SESSION_ID],
        "authorization_version": 1,
        "password_hash": generate_password_hash("CurrentSecure1!"),
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "daily-drive-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user["id"]
        flask_session["session_id"] = SESSION_ID
        flask_session["daily_reports_csrf"] = CSRF
    return store, client


def set_role(store, client, role, permissions=None):
    user = store.get_user(USER_ID)
    user["role"] = role
    user["custom_staff_type"] = (
        role if role not in {"super_admin", "office_staff", "supervisor"} else ""
    )
    store.save_user(user)
    if permissions is not None:
        store.save_custom_staff_type({
            "id": "daily-drive-custom-role",
            "name": role,
            "permissions": permissions,
        })
    with client.session_transaction() as flask_session:
        flask_session["role"] = role
        flask_session["permissions"] = list(permissions or [])


def configure_drive_oauth(monkeypatch, tmp_path):
    client_config = tmp_path / "oauth-client.json"
    client_config.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRETS_FILE", str(client_config))
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_FILE", str(tmp_path / "private" / "token.json"))
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://example.invalid/api/daily-reports/google-drive/callback",
    )
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", False)
    return client_config


def test_google_drive_status_requires_login_and_reports_setup_state(
    daily_drive_client, monkeypatch
):
    _store, client = daily_drive_client
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRETS_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)

    status = client.get("/api/daily-reports/google-drive/status")
    with client.session_transaction() as flask_session:
        flask_session.clear()
    unauthenticated = client.get("/api/daily-reports/google-drive/status")

    assert status.status_code == 200
    assert status.get_json() == {
        "connected": False,
        "configured": False,
        "can_connect": True,
        "message": "",
    }
    assert unauthenticated.status_code == 401


def test_all_daily_report_users_can_start_their_own_drive_oauth(daily_drive_client, monkeypatch):
    store, client = daily_drive_client
    state = "oauth-state-for-test"

    class FakeFlow:
        code_verifier = "pkce-code-verifier"

        def authorization_url(self, **options):
            assert options == {
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
            }
            return "https://accounts.google.com/o/oauth2/auth?state=test", state

    monkeypatch.setattr(app_module, "_daily_reports_drive_flow", lambda **_kwargs: FakeFlow())
    response = client.post(
        "/api/daily-reports/google-drive/connect",
        headers={"X-CSRF-Token": CSRF},
    )
    assert response.status_code == 200
    assert response.get_json()["authorization_url"].startswith("https://accounts.google.com/")
    with client.session_transaction() as flask_session:
        assert flask_session["daily_reports_drive_oauth_state"] == state
        assert flask_session["daily_reports_drive_code_verifier"] == "pkce-code-verifier"

    set_role(store, client, "office_staff")
    allowed = client.post(
        "/api/daily-reports/google-drive/connect",
        headers={"X-CSRF-Token": CSRF},
    )
    assert allowed.status_code == 200

    set_role(store, client, "Site Observer", ["daily_reports.read"])
    read_only_allowed = client.post(
        "/api/daily-reports/google-drive/connect",
        headers={"X-CSRF-Token": CSRF},
    )
    assert read_only_allowed.status_code == 200


def test_google_drive_callback_checks_oauth_state_before_saving(
    daily_drive_client, monkeypatch
):
    _store, client = daily_drive_client
    saved_credentials = []
    exchanged_codes = []

    class FakeFlow:
        credentials = object()

        def fetch_token(self, *, code):
            exchanged_codes.append(code)

    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_flow",
        lambda **_kwargs: FakeFlow(),
    )
    monkeypatch.setattr(
        app_module,
        "_save_daily_reports_drive_credentials",
        lambda user_id, credentials: saved_credentials.append((user_id, credentials)),
    )
    with client.session_transaction() as flask_session:
        flask_session["daily_reports_drive_oauth_state"] = "expected-state"
        flask_session["daily_reports_drive_code_verifier"] = "stored-verifier"

    bad_state = client.get(
        "/api/daily-reports/google-drive/callback?state=wrong&code=authorization-code"
    )
    assert bad_state.status_code == 302
    assert saved_credentials == []
    with client.session_transaction() as flask_session:
        flask_session["daily_reports_drive_oauth_state"] = "expected-state"
        flask_session["daily_reports_drive_code_verifier"] = "stored-verifier"

    success = client.get(
        "/api/daily-reports/google-drive/callback?state=expected-state&code=authorization-code"
    )
    assert success.status_code == 302
    assert exchanged_codes == ["authorization-code"]
    assert len(saved_credentials) == 1
    assert saved_credentials[0] == (USER_ID, FakeFlow.credentials)


def test_drive_token_files_are_isolated_per_user(monkeypatch, tmp_path):
    configure_drive_oauth(monkeypatch, tmp_path)
    first_user_path = app_module._daily_reports_drive_token_path("user-one")
    second_user_path = app_module._daily_reports_drive_token_path("user-two")

    assert first_user_path != second_user_path
    assert first_user_path.parent == second_user_path.parent
    assert "user-one" not in first_user_path.name
    assert "user-two" not in second_user_path.name


def test_callback_reports_writable_durable_storage_setup_for_permission_error(
    daily_drive_client, monkeypatch, caplog
):
    _store, client = daily_drive_client

    class FakeFlow:
        credentials = object()

        def fetch_token(self, *, code):
            pass

    def fail_to_save_credentials(user_id, credentials):
        raise PermissionError("sensitive path and content")

    monkeypatch.setattr(app_module, "_daily_reports_drive_flow", lambda **_kwargs: FakeFlow())
    monkeypatch.setattr(
        app_module,
        "_save_daily_reports_drive_credentials",
        fail_to_save_credentials,
    )
    with client.session_transaction() as flask_session:
        flask_session["daily_reports_drive_oauth_state"] = "expected-state"
        flask_session["daily_reports_drive_code_verifier"] = "stored-verifier"

    response = client.get(
        "/api/daily-reports/google-drive/callback?state=expected-state&code=authorization-code"
    )

    assert response.status_code == 302
    assert "category=token_storage_not_writable" in caplog.text
    assert "sensitive path and content" not in caplog.text
    with client.session_transaction() as flask_session:
        assert "writable durable storage" in flask_session["daily_reports_drive_message"]


def test_upload_requires_daily_report_write_permission_and_csrf(daily_drive_client):
    store, client = daily_drive_client
    set_role(store, client, "Site Observer", ["daily_reports.read"])
    denied = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={"photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "site.png", "image/png")},
    )
    assert denied.status_code == 403

    set_role(store, client, "office_staff")
    no_csrf = client.post(
        "/api/daily-reports/evidence/upload",
        data={"photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "site.png", "image/png")},
    )
    assert no_csrf.status_code == 403


def test_upload_rejects_unsupported_or_spoofed_image(daily_drive_client):
    _store, client = daily_drive_client
    response = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={"photo": (io.BytesIO(b"not an image"), "site.png", "image/png")},
    )
    assert response.status_code == 400
    assert "supported image" in response.get_json()["error"]


def test_upload_stores_photo_as_private_drive_file_and_returns_evidence_link(
    daily_drive_client, monkeypatch, tmp_path
):
    _store, client = daily_drive_client
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_credentials",
        lambda user_id: object(),
    )
    created_metadata = []
    created_media = []

    class FakeUploadRequest:
        def execute(self):
            return {
                "id": "private-drive-file-id",
                "webViewLink": "https://drive.google.com/file/d/private-drive-file-id/view",
            }

    class FakeFiles:
        def create(self, *, body, media_body, fields):
            created_metadata.append(body)
            created_media.append(media_body)
            assert fields == "id,webViewLink"
            return FakeUploadRequest()

    class FakeDrive:
        def files(self):
            return FakeFiles()

    monkeypatch.setattr(app_module, "build", lambda *_args, **_kwargs: FakeDrive())
    response = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={
            "photo": (
                io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"),
                "personal-filename.png",
                "image/png",
            )
        },
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "url": "https://drive.google.com/file/d/private-drive-file-id/view",
        "title": "Site photo",
    }
    assert response.headers["Cache-Control"] == "private, no-store"
    assert created_metadata[0]["name"].startswith("Daily Report Photo ")
    assert "personal-filename" not in created_metadata[0]["name"]
    assert "permissions" not in created_metadata[0]
    assert created_media[0]._mimetype == "image/png"


def test_upload_enforces_size_limit_before_reading_file(daily_drive_client):
    _store, client = daily_drive_client
    response = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={"photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "site.png", "image/png")},
        environ_overrides={"CONTENT_LENGTH": str(app_module.DAILY_REPORTS_MAX_IMAGE_BYTES + 300_000)},
    )
    assert response.status_code == 413
    assert "20 MB" in response.get_json()["error"]


def test_upload_is_disabled_until_drive_is_connected(daily_drive_client, monkeypatch, tmp_path):
    _store, client = daily_drive_client
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_credentials",
        lambda user_id: None,
    )

    response = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={"photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "site.png", "image/png")},
    )

    assert response.status_code == 503
    assert "not connected" in response.get_json()["error"]


def test_drive_upload_failure_does_not_log_or_return_sensitive_exception_details(
    daily_drive_client, monkeypatch, tmp_path, caplog
):
    _store, client = daily_drive_client
    configure_drive_oauth(monkeypatch, tmp_path)
    sensitive_marker = "private-token-value-for-test-only"

    def fail_to_load_credentials(user_id):
        raise RuntimeError(sensitive_marker)

    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_credentials",
        fail_to_load_credentials,
    )
    response = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={
            "photo": (
                io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"),
                "site.png",
                "image/png",
            )
        },
    )

    assert response.status_code == 503
    assert "Unable to upload the photo" in response.get_json()["error"]
    assert sensitive_marker not in response.get_data(as_text=True)
    assert sensitive_marker not in caplog.text


def test_daily_reports_ui_integrates_camera_capture_with_existing_evidence():
    source = (app_module.Path(__file__).parents[1] / "static" / "daily_reports.js").read_text(
        encoding="utf-8"
    )
    template = (
        app_module.Path(__file__).parents[1] / "templates" / "daily_reports.html"
    ).read_text(encoding="utf-8")

    assert 'capture="environment"' in source
    assert '"/api/daily-reports/evidence/upload"' in source
    assert 'row.querySelector(".dr-evurl").value = payload.url;' in source
    assert "connectButton.hidden = !payload.can_connect || Boolean(payload.connected);" in source
    assert 'id="dr-connectDrive"' in template
    assert "{% if current_user.role == 'super_admin' %}" not in template
