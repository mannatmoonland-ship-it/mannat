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
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://example.invalid/api/daily-reports/google-drive/callback",
    )
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", False)
    return client_config


def test_production_drive_oauth_uses_render_url_instead_of_stale_tunnel(
    monkeypatch, tmp_path
):
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://mannat.onrender.com")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://camera.indianplans.in/api/daily-reports/google-drive/callback",
    )

    _client_path, redirect_uri = app_module._daily_reports_drive_paths()

    assert redirect_uri == (
        "https://mannat.onrender.com/api/daily-reports/google-drive/callback"
    )


def test_production_drive_oauth_prefers_configured_public_domain(
    monkeypatch, tmp_path
):
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "https://reports.example.com")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://mannat.onrender.com")

    _client_path, redirect_uri = app_module._daily_reports_drive_paths()

    assert redirect_uri == (
        "https://reports.example.com/api/daily-reports/google-drive/callback"
    )


def test_production_drive_oauth_does_not_fall_back_to_explicit_redirect(
    monkeypatch, tmp_path
):
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)

    with pytest.raises(RuntimeError, match="public base URL is not configured"):
        app_module._daily_reports_drive_paths()


def test_local_drive_oauth_uses_request_localhost_not_stale_environment(
    monkeypatch, tmp_path
):
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://camera.indianplans.in/oauth2callback",
    )
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://mannat.onrender.com")
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "https://old.example.test")

    with app.test_request_context("/", base_url="http://localhost:5020"):
        _client_path, redirect_uri = app_module._daily_reports_drive_paths()

    assert redirect_uri == (
        "http://localhost:5020/api/daily-reports/google-drive/callback"
    )


def test_local_drive_oauth_rejects_non_localhost_access(
    monkeypatch, tmp_path
):
    configure_drive_oauth(monkeypatch, tmp_path)

    with app.test_request_context("/", base_url="http://192.168.1.8:5020"):
        with pytest.raises(RuntimeError, match="Open the local app at http://localhost"):
            app_module._daily_reports_drive_paths()


def test_connect_and_token_exchange_use_identical_local_redirect_uri(
    daily_drive_client, monkeypatch, tmp_path
):
    _store, client = daily_drive_client
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://camera.indianplans.in/oauth2callback",
    )
    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_token_store",
        lambda: object(),
    )
    used_redirect_uris = []

    class FakeFlow:
        credentials = object()
        code_verifier = "local-test-verifier"

        def __init__(self, redirect_uri):
            self.redirect_uri = redirect_uri

        def authorization_url(self, **_options):
            return "https://accounts.google.com/o/oauth2/auth?state=local-state", "local-state"

        def fetch_token(self, *, code):
            assert code == "local-authorization-code"

    def make_flow(_client_path, **options):
        used_redirect_uris.append(options["redirect_uri"])
        return FakeFlow(options["redirect_uri"])

    monkeypatch.setattr(
        app_module.Flow,
        "from_client_secrets_file",
        make_flow,
    )
    monkeypatch.setattr(
        app_module,
        "_save_daily_reports_drive_credentials",
        lambda _user_id, _credentials: None,
    )

    connect = client.post(
        "/api/daily-reports/google-drive/connect",
        base_url="http://localhost:5020",
        headers={"X-CSRF-Token": CSRF},
    )
    callback = client.get(
        "/api/daily-reports/google-drive/callback"
        "?state=local-state&code=local-authorization-code",
        base_url="http://localhost:5020",
    )

    expected_redirect_uri = (
        "http://localhost:5020/api/daily-reports/google-drive/callback"
    )
    assert connect.status_code == 200
    assert callback.status_code == 302
    assert used_redirect_uris == [expected_redirect_uri, expected_redirect_uri]


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
    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_token_store",
        lambda: object(),
    )

    class FakeFlow:
        code_verifier = "pkce-code-verifier"
        redirect_uri = "http://localhost/api/daily-reports/google-drive/callback"

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
        assert flask_session["daily_reports_drive_redirect_uri"] == FakeFlow.redirect_uri

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

    created_flows = []

    def make_flow(**options):
        created_flows.append(options)
        return FakeFlow()

    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_flow",
        make_flow,
    )
    monkeypatch.setattr(
        app_module,
        "_save_daily_reports_drive_credentials",
        lambda user_id, credentials: saved_credentials.append((user_id, credentials)),
    )
    with client.session_transaction() as flask_session:
        flask_session["daily_reports_drive_oauth_state"] = "expected-state"
        flask_session["daily_reports_drive_code_verifier"] = "stored-verifier"
        flask_session["daily_reports_drive_redirect_uri"] = (
            "http://localhost/api/daily-reports/google-drive/callback"
        )

    bad_state = client.get(
        "/api/daily-reports/google-drive/callback?state=wrong&code=authorization-code"
    )
    assert bad_state.status_code == 302
    assert saved_credentials == []
    with client.session_transaction() as flask_session:
        flask_session["daily_reports_drive_oauth_state"] = "expected-state"
        flask_session["daily_reports_drive_code_verifier"] = "stored-verifier"
        flask_session["daily_reports_drive_redirect_uri"] = (
            "http://localhost/api/daily-reports/google-drive/callback"
        )

    success = client.get(
        "/api/daily-reports/google-drive/callback?state=expected-state&code=authorization-code"
    )
    assert success.status_code == 302
    assert exchanged_codes == ["authorization-code"]
    assert created_flows[0]["redirect_uri"] == (
        "http://localhost/api/daily-reports/google-drive/callback"
    )
    assert len(saved_credentials) == 1
    assert saved_credentials[0] == (USER_ID, FakeFlow.credentials)


def test_drive_status_uses_authenticated_user_not_query_parameter(
    daily_drive_client, monkeypatch, tmp_path
):
    _store, client = daily_drive_client
    configure_drive_oauth(monkeypatch, tmp_path)
    requested_users = []

    class FakeTokenStore:
        def load(self, user_id):
            requested_users.append(user_id)
            return None

    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_token_store",
        lambda: FakeTokenStore(),
    )

    response = client.get(
        "/api/daily-reports/google-drive/status?user_id=another-user"
    )

    assert response.status_code == 200
    assert requested_users == [USER_ID]


def test_callback_reports_firestore_storage_failure_without_details(
    daily_drive_client, monkeypatch, caplog
):
    _store, client = daily_drive_client

    class FakeFlow:
        credentials = object()

        def fetch_token(self, *, code):
            pass

    def fail_to_save_credentials(user_id, credentials):
        raise app_module.TokenStorageUnavailable("sensitive path and content")

    monkeypatch.setattr(app_module, "_daily_reports_drive_flow", lambda **_kwargs: FakeFlow())
    monkeypatch.setattr(
        app_module,
        "_save_daily_reports_drive_credentials",
        fail_to_save_credentials,
    )
    with client.session_transaction() as flask_session:
        flask_session["daily_reports_drive_oauth_state"] = "expected-state"
        flask_session["daily_reports_drive_code_verifier"] = "stored-verifier"
        flask_session["daily_reports_drive_redirect_uri"] = (
            "http://localhost/api/daily-reports/google-drive/callback"
        )

    response = client.get(
        "/api/daily-reports/google-drive/callback?state=expected-state&code=authorization-code"
    )

    assert response.status_code == 302
    assert "category=token_storage_unavailable" in caplog.text
    assert "sensitive path and content" not in caplog.text
    with client.session_transaction() as flask_session:
        assert "connection storage is unavailable" in flask_session["daily_reports_drive_message"]


def test_expired_credentials_are_refreshed_and_persisted_with_version_check(monkeypatch):
    from daily_reports_drive_tokens import StoredCredentials

    class FakeCredentials:
        expired = True
        refresh_token = "refresh-token-for-test"
        valid = False

        def refresh(self, _request):
            self.expired = False
            self.valid = True

        def to_json(self):
            return '{"token":"refreshed-token"}'

    class FakeTokenStore:
        def __init__(self):
            self.saved = []

        def load(self, user_id):
            assert user_id == USER_ID
            return StoredCredentials('{"token":"expired-token"}', 4)

        def save_if_version(self, user_id, serialized, expected_version):
            self.saved.append((user_id, serialized, expected_version))
            return True

    token_store = FakeTokenStore()
    credentials = FakeCredentials()
    monkeypatch.setattr(app_module, "_daily_reports_drive_token_store", lambda: token_store)
    monkeypatch.setattr(
        app_module.Credentials,
        "from_authorized_user_info",
        staticmethod(lambda _info, scopes: credentials),
    )

    result = app_module._daily_reports_drive_credentials(USER_ID)

    assert result is credentials
    assert token_store.saved == [(USER_ID, '{"token":"refreshed-token"}', 4)]


def test_invalid_grant_requires_drive_reconnection(monkeypatch):
    from google.auth.exceptions import RefreshError
    from daily_reports_drive_tokens import StoredCredentials

    class FakeCredentials:
        expired = True
        refresh_token = "revoked-refresh-token"
        valid = False

        def refresh(self, _request):
            raise RefreshError("invalid_grant: revoked")

    class FakeTokenStore:
        def load(self, _user_id):
            return StoredCredentials('{"token":"expired-token"}', 1)

    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_token_store",
        lambda: FakeTokenStore(),
    )
    monkeypatch.setattr(
        app_module.Credentials,
        "from_authorized_user_info",
        staticmethod(lambda _info, scopes: FakeCredentials()),
    )

    assert app_module._daily_reports_drive_credentials(USER_ID) is None


def test_refresh_conflict_uses_newer_credentials_without_overwriting_them(monkeypatch):
    from daily_reports_drive_tokens import StoredCredentials

    class FakeCredentials:
        def __init__(self, valid):
            self.expired = not valid
            self.refresh_token = "refresh-token-for-test"
            self.valid = valid

        def refresh(self, _request):
            self.valid = True
            self.expired = False

        def to_json(self):
            return '{"token":"refreshed-stale-token"}'

    class FakeTokenStore:
        def __init__(self):
            self.reads = 0

        def load(self, _user_id):
            self.reads += 1
            if self.reads == 1:
                return StoredCredentials('{"token":"old"}', 1)
            return StoredCredentials('{"token":"newer"}', 2)

        def save_if_version(self, _user_id, _serialized, _expected_version):
            return False

    credentials_by_payload = {
        '{"token":"old"}': FakeCredentials(False),
        '{"token":"newer"}': FakeCredentials(True),
    }
    token_store = FakeTokenStore()
    monkeypatch.setattr(app_module, "_daily_reports_drive_token_store", lambda: token_store)
    monkeypatch.setattr(
        app_module.Credentials,
        "from_authorized_user_info",
        staticmethod(lambda info, scopes: credentials_by_payload[
            '{"token":"' + info["token"] + '"}'
        ]),
    )

    result = app_module._daily_reports_drive_credentials(USER_ID)

    assert result is credentials_by_payload['{"token":"newer"}']
    assert token_store.reads == 2


def test_upload_requires_daily_report_write_permission_and_csrf(daily_drive_client):
    store, client = daily_drive_client
    set_role(store, client, "Site Observer", ["daily_reports.read"])
    denied = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={
            "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "site.png", "image/png"),
            "upload_id": "bd13c526-d16b-4dc8-8764-889fc077573a",
        },
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


def test_upload_shares_photo_and_returns_drive_evidence_metadata(
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
    calls = []
    upload_id = "be85e310-8c2b-4273-a986-7c015c9e2d44"

    class FakeRequest:
        def __init__(self, result=None):
            self.result = result

        def execute(self):
            return self.result or {}

    class FakeFiles:
        def list(self, **options):
            calls.append(("list", options))
            return FakeRequest({"files": []})

        def create(self, *, body, media_body, fields):
            created_metadata.append(body)
            created_media.append(media_body)
            calls.append(("upload", body))
            assert fields == "id,webViewLink,mimeType,appProperties"
            return FakeRequest({
                "id": "shared-drive-file-id",
                "webViewLink": "https://drive.google.com/file/d/shared-drive-file-id/view",
                "mimeType": "image/png",
                "appProperties": body["appProperties"],
            })

    class FakePermissions:
        def list(self, **options):
            calls.append(("permission_list", options))
            return FakeRequest({"permissions": []})

        def create(self, **options):
            calls.append(("permission_create", options))
            return FakeRequest({"id": "anyone-permission"})

    class FakeDrive:
        def files(self):
            return FakeFiles()

        def permissions(self):
            return FakePermissions()

    monkeypatch.setattr(app_module, "build", lambda *_args, **_kwargs: FakeDrive())
    response = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={
            "photo": (
                io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"),
                "personal-filename.png",
                "image/png",
            ),
            "upload_id": upload_id,
        },
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "url": "https://drive.google.com/file/d/shared-drive-file-id/view",
        "preview_url": (
            "https://drive.google.com/thumbnail?id=shared-drive-file-id&sz=w1600"
        ),
        "file_id": "shared-drive-file-id",
        "title": "Site photo",
    }
    assert response.headers["Cache-Control"] == "private, no-store"
    assert created_metadata[0]["name"].startswith("Daily Report Photo ")
    assert "personal-filename" not in created_metadata[0]["name"]
    assert created_metadata[0]["appProperties"] == {
        app_module.DAILY_REPORTS_DRIVE_UPLOAD_ID_PROPERTY: upload_id,
        app_module.DAILY_REPORTS_DRIVE_UPLOAD_HASH_PROPERTY: app_module.hashlib.sha256(
            b"\x89PNG\r\n\x1a\nimage-data"
        ).hexdigest(),
        app_module.DAILY_REPORTS_DRIVE_UPLOAD_OWNER_PROPERTY: app_module.hashlib.sha256(
            USER_ID.encode("utf-8")
        ).hexdigest()[:32],
    }
    assert created_media[0]._mimetype == "image/png"
    assert [call[0] for call in calls] == [
        "list",
        "upload",
        "permission_list",
        "permission_create",
    ]
    assert calls[-1][1]["body"] == {"type": "anyone", "role": "reader"}
    assert calls[-1][1]["fileId"] == "shared-drive-file-id"


def test_upload_permission_failure_does_not_report_public_photo_success(
    daily_drive_client, monkeypatch, tmp_path
):
    _store, client = daily_drive_client
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_credentials",
        lambda _user_id: object(),
    )

    class FakeRequest:
        def __init__(self, result=None, error=None):
            self.result = result or {}
            self.error = error

        def execute(self):
            if self.error:
                raise self.error
            return self.result

    class FakeFiles:
        def list(self, **_options):
            return FakeRequest({"files": []})

        def create(self, *, body, **_options):
            return FakeRequest({
                "id": "uploaded-but-private",
                "webViewLink": "https://drive.google.com/file/d/uploaded-but-private/view",
                "mimeType": "image/png",
                "appProperties": body["appProperties"],
            })

    class FakePermissions:
        def list(self, **_options):
            return FakeRequest({"permissions": []})

        def create(self, **_options):
            return FakeRequest(error=RuntimeError("private API details"))

    class FakeDrive:
        def files(self):
            return FakeFiles()

        def permissions(self):
            return FakePermissions()

    monkeypatch.setattr(app_module, "build", lambda *_args, **_kwargs: FakeDrive())
    response = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={
            "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "site.png", "image/png"),
            "upload_id": "c45a0f9a-81e8-461e-819c-4c2df45fceee",
        },
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "error": (
            "The photo was uploaded, but link sharing could not be enabled. "
            "Retry the upload to finish sharing."
        )
    }
    assert "url" not in response.get_json()
    assert "file_id" not in response.get_json()


def test_retry_reuses_uploaded_photo_after_permission_failure(
    daily_drive_client, monkeypatch, tmp_path
):
    _store, client = daily_drive_client
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_credentials",
        lambda _user_id: object(),
    )
    upload_id = "01b45e7f-12f2-4906-9b78-e6a4e0d256ab"
    uploaded_metadata = {
        "id": "retry-safe-file-id",
        "webViewLink": "https://drive.google.com/file/d/retry-safe-file-id/view",
        "mimeType": "image/png",
    }
    create_calls = []
    permission_create_calls = []
    attempts = 0

    class FakeRequest:
        def __init__(self, result=None, error=None):
            self.result = result or {}
            self.error = error

        def execute(self):
            if self.error:
                raise self.error
            return self.result

    class FakeFiles:
        def list(self, **options):
            if create_calls:
                return FakeRequest({
                    "files": [{
                        **uploaded_metadata,
                        "appProperties": create_calls[0]["body"]["appProperties"],
                    }]
                })
            return FakeRequest({"files": []})

        def create(self, **options):
            create_calls.append(options)
            return FakeRequest({
                **uploaded_metadata,
                "appProperties": options["body"]["appProperties"],
            })

    class FakePermissions:
        def list(self, **_options):
            return FakeRequest({"permissions": []})

        def create(self, **options):
            nonlocal attempts
            permission_create_calls.append(options)
            attempts += 1
            if attempts == 1:
                return FakeRequest(error=RuntimeError("temporary permission failure"))
            return FakeRequest({"id": "anyone-permission"})

    class FakeDrive:
        def files(self):
            return FakeFiles()

        def permissions(self):
            return FakePermissions()

    monkeypatch.setattr(app_module, "build", lambda *_args, **_kwargs: FakeDrive())

    def post_upload():
        return client.post(
            "/api/daily-reports/evidence/upload",
            headers={"X-CSRF-Token": CSRF},
            data={
                "photo": (
                    io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"),
                    "site.png",
                    "image/png",
                ),
                "upload_id": upload_id,
            },
        )

    failed_attempt = post_upload()
    retry = post_upload()

    assert failed_attempt.status_code == 503
    assert retry.status_code == 201
    assert retry.get_json()["file_id"] == uploaded_metadata["id"]
    assert len(create_calls) == 1
    assert len(permission_create_calls) == 2
    assert permission_create_calls[0]["fileId"] == permission_create_calls[1]["fileId"]


def test_upload_uses_drive_file_id_to_build_view_url_when_missing(
    daily_drive_client, monkeypatch, tmp_path
):
    _store, client = daily_drive_client
    configure_drive_oauth(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module,
        "_daily_reports_drive_credentials",
        lambda _user_id: object(),
    )

    class FakeRequest:
        def __init__(self, result):
            self.result = result

        def execute(self):
            return self.result

    class FakeFiles:
        def list(self, **_options):
            return FakeRequest({"files": []})

        def create(self, *, body, **_options):
            return FakeRequest({
                "id": "fallback-preview-id",
                "mimeType": "image/png",
                "appProperties": body["appProperties"],
            })

    class FakePermissions:
        def list(self, **_options):
            return FakeRequest({"permissions": []})

        def create(self, **_options):
            return FakeRequest({"id": "anyone-permission"})

    class FakeDrive:
        def files(self):
            return FakeFiles()

        def permissions(self):
            return FakePermissions()

    monkeypatch.setattr(app_module, "build", lambda *_args, **_kwargs: FakeDrive())
    response = client.post(
        "/api/daily-reports/evidence/upload",
        headers={"X-CSRF-Token": CSRF},
        data={
            "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "site.png", "image/png"),
            "upload_id": "101c0a77-5f22-4600-9f5a-4b7803d0f0df",
        },
    )

    assert response.status_code == 201
    assert response.get_json()["url"] == (
        "https://drive.google.com/file/d/fallback-preview-id/view"
    )
    assert response.get_json()["preview_url"] == (
        "https://drive.google.com/thumbnail?id=fallback-preview-id&sz=w1600"
    )
    assert response.get_json()["file_id"] == "fallback-preview-id"


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
        data={
            "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "site.png", "image/png"),
            "upload_id": "1a21a131-783a-4e96-919b-43d409c07da9",
        },
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
            ),
            "upload_id": "963079b1-abec-4751-a528-27a5f312228c",
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
    assert 'row.dataset.driveFileId = payload.file_id;' in source
    assert 'row.dataset.drivePreviewUrl = payload.preview_url;' in source
    assert '...(row.dataset.driveFileId ? { fileId: row.dataset.driveFileId } : {})' in source
    assert '...(row.dataset.drivePreviewUrl ? { previewUrl: row.dataset.drivePreviewUrl } : {})' in source
    assert "formData.append(\"upload_id\", row.dataset.driveUploadId);" in source
    assert "connectButton.hidden = !payload.can_connect || Boolean(payload.connected);" in source
    assert 'id="dr-connectDrive"' in template
    assert "Anyone who obtains the photo link may be able to view or download it." in template
    assert "{% if current_user.role == 'super_admin' %}" not in template


def test_daily_reports_upload_updates_evidence_metadata_and_preview_immediately():
    source = (
        app_module.Path(__file__).parents[1] / "static" / "daily_reports.js"
    ).read_text(encoding="utf-8")
    upload_flow = source[
        source.index('uploadInput.addEventListener("change"'):
        source.index("function initializeDailyReportsDrive")
    ]
    submit_flow = source[
        source.index("async function handleFormSubmit"):
        source.index("async function resolveEvidenceImageUrl")
    ]

    assert "row.querySelector(\".dr-evurl\").value = payload.url;" in upload_flow
    assert "row.dataset.driveFileId = payload.file_id;" in upload_flow
    assert "row.dataset.drivePreviewUrl = payload.preview_url;" in upload_flow
    assert upload_flow.index("row.dataset.drivePreviewUrl = payload.preview_url;") < upload_flow.index("updatePreview();")
    assert '...(row.dataset.driveFileId ? { fileId: row.dataset.driveFileId } : {})' in submit_flow
    assert '...(row.dataset.drivePreviewUrl ? { previewUrl: row.dataset.drivePreviewUrl } : {})' in submit_flow
    assert "updateDisplayedDailyRecord(responseData.id, responseData.record);" in submit_flow
    assert "await loadRecords(" not in submit_flow
    assert "loadRecords({ serverOnly" not in source
    assert submit_flow.index("updateDisplayedDailyRecord(responseData.id") < submit_flow.rindex(
        'showToast(\n      isEditing ? "Daily Record updated successfully."'
    )
    assert "Daily Record was saved, but the report card could not be updated." in submit_flow


def test_daily_reports_save_error_is_limited_to_failed_persistence_request():
    source = (
        app_module.Path(__file__).parents[1] / "static" / "daily_reports.js"
    ).read_text(encoding="utf-8")
    submit_flow = source[
        source.index("async function handleFormSubmit"):
        source.index("async function resolveEvidenceImageUrl")
    ]
    save_error_boundary = submit_flow[
        submit_flow.index("const response = await fetch("):
        submit_flow.index("editingId = null;")
    ]

    assert "if (!response.ok)" in save_error_boundary
    assert "throw new Error(responseData.error || \"Unable to save the daily report.\")" in save_error_boundary
    assert "return;" in save_error_boundary[save_error_boundary.index("} catch (error) {"):]
    assert "await loadRecords({ serverOnly: true" not in save_error_boundary
    assert submit_flow.index("showToast(error.message || \"Unable to save") < submit_flow.index(
        "editingId = null;"
    )
    assert submit_flow.index("updateDisplayedDailyRecord(responseData.id") < submit_flow.rindex(
        'showToast(\n      isEditing ? "Daily Record updated successfully."'
    )
    assert "await loadRecords(" not in submit_flow


def test_daily_reports_update_one_record_card_and_deduplicate_by_drive_file_id():
    source = (
        app_module.Path(__file__).parents[1] / "static" / "daily_reports.js"
    ).read_text(encoding="utf-8")
    update_function = source[
        source.index("function updateDisplayedDailyRecord"):
        source.index("async function loadRecords")
    ]
    render_function = source[
        source.index("function renderDailyRecordCard"):
        source.index("function updateDisplayedDailyRecord")
    ]

    assert 'card.dataset.recordId === recordId' in update_function
    assert 'existingCard.outerHTML = cardMarkup;' in update_function
    assert 'recordsElement.insertAdjacentHTML("beforeend", cardMarkup);' in update_function
    assert 'data-evidence-file-id="${escapeHtml(item.fileId || "")}"' in render_function
    assert 'previewHtml(item.type, item.url, item.previewUrl)' in render_function
    assert "loading=\"${eagerImage ? \"eager\" : \"lazy\"}\"" in source
    assert "getUniqueDailyReportEvidence(record)" in render_function
    assert "seenFileIds.has(item.fileId)" in source
