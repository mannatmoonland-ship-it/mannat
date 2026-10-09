import ssl
from types import SimpleNamespace

import google.auth.credentials
import google.auth.exceptions
import google.auth.transport.urllib3
import pytest
from google.api_core.exceptions import DeadlineExceeded, PermissionDenied

import app as app_module


def test_urllib3_firestore_channel_uses_verified_google_auth_request(monkeypatch):
    captured = {}

    def fake_authorized_channel(credentials, request, target, ssl_credentials=None, **kwargs):
        captured.update(
            credentials=credentials,
            request=request,
            target=target,
            ssl_credentials=ssl_credentials,
            kwargs=kwargs,
        )
        return object()

    monkeypatch.setattr(
        app_module.google.auth.transport.grpc,
        "secure_authorized_channel",
        fake_authorized_channel,
    )
    monkeypatch.setattr(app_module, "_FIRESTORE_AUTH_REQUEST", None)
    monkeypatch.setattr(app_module, "_FIRESTORE_AUTH_POOL", None)

    channel = app_module._Urllib3FirestoreGrpcTransport.create_channel(
        "firestore.googleapis.com",
        credentials=google.auth.credentials.AnonymousCredentials(),
        options=(("grpc.keepalive_time_ms", 30000),),
    )

    request = captured["request"]
    tls_context = request.http.connection_pool_kw["ssl_context"]
    assert channel is not None
    assert isinstance(request, google.auth.transport.urllib3.Request)
    assert captured["target"] == "firestore.googleapis.com"
    assert captured["kwargs"]["options"] == (("grpc.keepalive_time_ms", 30000),)
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
    assert tls_context.check_hostname is True


def test_firestore_client_reads_transport_setting_during_initialization(monkeypatch, caplog):
    class FirebaseApp:
        project_id = "isolated-project"
        credential = SimpleNamespace(get_credential=lambda: "firebase-credential")

    firebase_app = FirebaseApp()
    selected = {}

    class FirebaseAdmin:
        @staticmethod
        def get_app():
            raise ValueError("no existing app")

        @staticmethod
        def initialize_app(credential, options, name=None):
            selected["firebase_admin_credential"] = credential
            selected["options"] = options
            return firebase_app

    class ApplicationDefault:
        pass

    def custom_client(**kwargs):
        selected["custom_client"] = kwargs
        return "custom-firestore-client"

    monkeypatch.setattr(app_module, "firebase_admin", FirebaseAdmin)
    monkeypatch.setattr(
        app_module,
        "credentials",
        SimpleNamespace(
            ApplicationDefault=ApplicationDefault,
            Certificate=lambda _path: pytest.fail("Unexpected service-account file access"),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "_Urllib3FirestoreClient",
        custom_client,
    )
    monkeypatch.setenv("FIRESTORE_GRPC_AUTH_TRANSPORT", "urllib3")
    caplog.set_level("INFO", logger="app")

    store = app_module.FirebaseFirestoreStore(
        project_id="isolated-project",
        service_account_path="missing-test-credential.json",
        app_name="isolated-test-app",
    )

    assert store.firebase_app is firebase_app
    assert store.db == "custom-firestore-client"
    assert selected["custom_client"] == {
        "credentials": "firebase-credential",
        "project": "isolated-project",
    }
    assert "credential_source=application_default_credentials_configured_file_missing" in caplog.text
    assert "auth_transport=urllib3" in caplog.text


def test_cached_firestore_client_is_recreated_when_transport_setting_changes(monkeypatch):
    cached_client = object()
    replacement_client = object()
    constructed = []

    def create_store(**kwargs):
        constructed.append(kwargs)
        return replacement_client

    monkeypatch.setitem(app_module.app.config, "FIRESTORE_STORE", None)
    monkeypatch.setattr(app_module, "firebase_admin", object())
    monkeypatch.setattr(
        app_module,
        "get_active_firebase_config",
        lambda: {
            "project_id": "isolated-project",
            "service_account_path": "credential.json",
        },
    )
    monkeypatch.setattr(app_module, "FirebaseFirestoreStore", create_store)
    monkeypatch.setattr(app_module.app, "_firebase_store", cached_client, raising=False)
    monkeypatch.setattr(
        app_module.app,
        "_firebase_store_key",
        ("isolated-project", "credential.json", "grpc"),
        raising=False,
    )
    monkeypatch.setenv("FIRESTORE_GRPC_AUTH_TRANSPORT", "urllib3")

    store = app_module.get_user_store()

    assert store is replacement_client
    assert constructed == [{
        "project_id": "isolated-project",
        "service_account_path": "credential.json",
    }]
    assert app_module.app._firebase_store_key == (
        "isolated-project",
        "credential.json",
        "urllib3",
    )


def test_login_logs_root_firestore_failure_trace_without_sensitive_exception_text(
    monkeypatch, caplog
):
    secret_marker = "private-user@example.test"

    def unavailable_store():
        try:
            raise PermissionDenied(
                f"password=do-not-log {secret_marker} bearer=do-not-log-token"
            )
        except PermissionDenied as cause:
            raise RuntimeError("Firestore initialization failed") from cause

    monkeypatch.setattr(app_module, "get_user_store", unavailable_store)
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    monkeypatch.setitem(app_module.app.config, "SECRET_KEY", "transport-test-secret")
    caplog.set_level("ERROR", logger="app")

    with app_module.app.test_client() as client:
        response = client.post(
            "/login",
            data={"username": secret_marker, "password": "login-password"},
            follow_redirects=False,
        )

    assert response.status_code == 503
    assert b"Traceback" not in response.data
    assert secret_marker.encode() not in response.data
    assert "exception_chain=RuntimeError -> PermissionDenied" in caplog.text
    assert "category=permission_or_authentication" in caplog.text
    assert "unavailable_store" in caplog.text
    assert "password=do-not-log" not in caplog.text
    assert secret_marker not in caplog.text
    assert "do-not-log-token" not in caplog.text
    assert "login-password" not in caplog.text


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ssl.SSLError("test tls failure"), "tls"),
        (
            google.auth.exceptions.DefaultCredentialsError("test credential failure"),
            "credentials",
        ),
        (PermissionDenied("test permissions failure"), "permission_or_authentication"),
        (DeadlineExceeded("test timeout"), "timeout"),
        (OSError("test connectivity failure"), "connectivity"),
    ],
)
def test_firestore_failure_category_identifies_common_root_causes(error, category):
    assert app_module._firestore_failure_category(error) == category
