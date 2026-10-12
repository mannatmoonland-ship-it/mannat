import pytest

from app import app


class FakeStore:
    def __init__(self, role="super_admin"):
        self.user = {
            "id": "email-test-user",
            "role": role,
            "active_sessions": ["email-test-session"],
        }

    def get_user(self, user_id):
        return self.user if user_id == self.user["id"] else None


@pytest.fixture

def client(monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "email-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", FakeStore())
    monkeypatch.setitem(app.config, "MAIL_PASSWORD", "test-only-configured")
    return app.test_client()


def login(client, role="super_admin"):
    app.config["FIRESTORE_STORE"] = FakeStore(role)
    with client.session_transaction() as session:
        session["user_id"] = "email-test-user"
        session["session_id"] = "email-test-session"
    client.get("/test-email")


def csrf_token(client):
    with client.session_transaction() as session:
        return session["authenticated_form_csrf"]


def test_test_email_form_is_restricted_to_super_admin(client):
    login(client)
    response = client.get("/test-email")
    assert response.status_code == 200
    assert b"Send Test Email" in response.data
    assert b"recipient_email" in response.data

    login(client, "office_staff")
    response = client.get("/test-email")
    assert response.status_code == 302


def test_test_email_sends_requested_content_and_shows_success_after_sender_returns(client, monkeypatch):
    login(client)
    sent = []

    def fake_send_email(recipient, subject, body):
        sent.append((recipient, subject, body))
        return True

    monkeypatch.setattr("app.send_email", fake_send_email)

    response = client.post(
        "/test-email",
        data={"recipient_email": "recipient@example.com", "csrf_token": csrf_token(client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert sent == [(
        "recipient@example.com",
        "Mannat Moon Construction \u2013 Test Email",
        "Hello,\n\n"
        "This is a test email from the Mannat Moon Construction application.\n\n"
        "The email service is working, and the application can send emails successfully.\n\n"
        "Project: Mannat Moon Construction\n"
        "Purpose: Email Configuration Test\n\n"
        "Regards,\n"
        "Mannat Moon Construction Team",
    )]
    assert b"Test email sent successfully." in response.data


def test_test_email_logs_sanitized_smtp_diagnostics_only_to_server(client, monkeypatch, caplog):
    login(client)
    monkeypatch.setitem(app.config, "MAIL_SERVER", "smtp.example.test")
    monkeypatch.setitem(app.config, "MAIL_PORT", 587)
    monkeypatch.setitem(app.config, "MAIL_USE_TLS", True)
    monkeypatch.setitem(app.config, "MAIL_USERNAME", "smtp-user@example.test")
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", "smtp-user@example.test")
    monkeypatch.setitem(app.config, "MAIL_PASSWORD", "private-secret")

    def fail_send_email(*args):
        raise RuntimeError("auth failed username=smtp-user@example.test password=private-secret token=secret-token")

    monkeypatch.setattr("app.send_email", fail_send_email)

    response = client.post(
        "/test-email",
        data={"recipient_email": "recipient@example.com", "csrf_token": csrf_token(client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"could not be sent" in response.data
    assert b"private-secret" not in response.data
    assert b"secret-token" not in response.data
    assert "exception_class=RuntimeError" in caplog.text
    assert "smtp_host=smtp.example.test" in caplog.text
    assert "smtp_port=587" in caplog.text
    assert "tls_enabled=True" in caplog.text
    assert "[REDACTED]" in caplog.text
    assert "smtp-user@example.test" not in caplog.text
    assert "private-secret" not in caplog.text
    assert "secret-token" not in caplog.text
