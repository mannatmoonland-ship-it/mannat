import ssl

import google.auth.credentials
import google.auth.transport.urllib3

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
