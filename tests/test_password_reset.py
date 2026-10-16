import hashlib
from datetime import datetime, timedelta, timezone

import pytest

import app as app_module
from app import InMemoryFirestoreStore, app, generate_password_hash


USER_ID = "account-user-123"
OLD_PASSWORD = "OldSecure1!"
NEW_PASSWORD = "NewSecure2!"
GENERIC_RESPONSE = b"If an eligible account was found, reset instructions will be sent to its saved email address."


@pytest.fixture

def client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.save_user({
        "id": USER_ID,
        "username": "recovery-user",
        "full_name": "Recovery User",
        "email": "saved-account@example.com",
        "mobile_phone": "+919876543210",
        "mobile_phone_verified_at": "2025-01-01T00:00:00+00:00",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": generate_password_hash(OLD_PASSWORD),
        "must_change_password": False,
        "is_active": True,
        "active_sessions": [],
        "permissions": ["dashboard"],
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "password-reset-test-secret")
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "https://app.example.test")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    monkeypatch.setitem(app.config, "MAIL_PASSWORD", "mock-smtp-only")
    monkeypatch.setattr(app_module, "PASSWORD_RESET_ATTEMPTS", {})
    monkeypatch.setattr(app_module, "SMS_OTP_ATTEMPTS", {})
    monkeypatch.setattr(app_module, "_send_fast2sms_otp", lambda *_args: None)
    return app.test_client()


def csrf_token(client, base_url):
    with client.session_transaction(base_url=base_url) as session:
        return session["password_reset_csrf"]


def test_super_admin_recovery_page_links_to_regular_password_reset():
    response = app.test_client().get("/super-admin-recovery")

    assert response.status_code == 200
    assert b'href="/password-reset-request"' in response.data
    assert b"saved email" in response.data
    assert b"single-use recovery code" in response.data


def request_reset_link(client, user_id=USER_ID, base_url="http://office-test.example:5020", headers=None):
    client.get("/password-reset-request", base_url=base_url)
    token = csrf_token(client, base_url)
    response = client.post(
        "/password-reset-request",
        data={"csrf_token": token, "user_identifier": user_id},
        follow_redirects=True,
        base_url=base_url,
        headers=headers,
    )
    return response


def prepare_reset_link(client, base_url):
    raw_token = "local-development-reset-token"
    user = app.config["FIRESTORE_STORE"].get_user(USER_ID)
    user["password_reset_token_hash"] = hashlib.sha256(raw_token.encode()).hexdigest()
    user["password_reset_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(minutes=30)
    ).isoformat()
    app.config["FIRESTORE_STORE"].save_user(user)
    client.get(f"/reset-password/{USER_ID}", base_url=base_url)
    csrf = csrf_token(client, base_url)
    return raw_token, csrf


def test_reset_page_has_email_link_only_flow():
    response = app.test_client().get("/reset-password/account-user-123")

    assert response.status_code == 200
    assert b"phoneChallenge" not in response.data
    assert b"SMS verification code" not in response.data
    assert b"data-send-code-url" not in response.data
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert f"/api/reset-password/<user_id>/send-code" not in rules
    assert f"/api/reset-password/<user_id>/verify-code" not in rules


def sign_in_as_admin(client, base_url="http://office-test.example:5020"):
    store = app.config["FIRESTORE_STORE"]
    store.save_user({
        "id": "admin-user",
        "username": "admin",
        "full_name": "Admin User",
        "role": "super_admin",
        "is_active": True,
        "active_sessions": ["admin-session"],
    })
    with client.session_transaction(base_url=base_url) as session:
        session["user_id"] = "admin-user"
        session["session_id"] = "admin-session"
        session["role"] = "super_admin"
    client.get("/user-management", base_url=base_url)


def authenticated_form_csrf(client, base_url="http://office-test.example:5020"):
    with client.session_transaction(base_url=base_url) as session:
        return session["authenticated_form_csrf"]


def test_valid_user_id_sends_link_only_to_saved_email_and_stores_hash(client, monkeypatch):
    assert b"Forgot Password?" in client.get("/login").data
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    response = request_reset_link(client)

    assert b"Password reset instructions have been sent to s***********t@example.com." in response.data
    assert b"saved-account@example.com" not in response.data
    assert len(sent) == 1
    (recipient, subject, body), options = sent[0]
    assert recipient == "saved-account@example.com"
    assert subject == "Mannat Moon \u2014 Password Reset Request"
    assert "html_body" in options
    assert "Password reset request" in options["html_body"]
    assert "Reset Password" in options["html_body"]
    assert "Hello Recovery User," in body
    assert "expires at" in body
    assert "If you did not request this reset" in body
    assert "https://app.example.test/reset-password/account-user-123#" in body
    raw_token = body.rsplit("#", 1)[1].splitlines()[0]
    stored_user = app.config["FIRESTORE_STORE"].get_user(USER_ID)
    assert stored_user["password_reset_token_hash"] == hashlib.sha256(raw_token.encode()).hexdigest()
    assert stored_user["password_reset_token_hash"] != raw_token
    expiry = datetime.fromisoformat(stored_user["password_reset_expires_at"])
    assert timedelta(minutes=29) < expiry - datetime.now(timezone.utc) < timedelta(minutes=31)


def test_username_lookup_uses_registered_email_and_document_id_in_link(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    request_reset_link(client, "recovery-user")

    (recipient, _subject, body), options = sent[0]
    assert recipient == "saved-account@example.com"
    assert "/reset-password/account-user-123#" in body
    assert "/reset-password/account-user-123#" in options["html_body"]


def test_reset_links_use_the_configured_production_origin(client, monkeypatch):
    sent = []
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    request_reset_link(
        client,
        base_url="https://attacker.example.com",
        headers={"Host": "attacker.example.com"},
    )

    assert len(sent) == 1
    assert f"https://app.example.test/reset-password/{USER_ID}#" in sent[0][0][2]
    assert "attacker.example.com" not in sent[0][0][2]


def test_missing_origin_uses_the_configured_canonical_https_origin(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    request_reset_link(client, headers={"Host": "office-test.example:5020"})

    assert len(sent) == 1
    assert f"https://app.example.test/reset-password/{USER_ID}#" in sent[0][0][2]


def test_development_reset_link_uses_private_lan_request_origin(client, monkeypatch):
    sent = []
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    request_reset_link(client, base_url="http://192.168.68.123:5020")

    assert len(sent) == 1
    assert f"http://192.168.68.123:5020/reset-password/{USER_ID}#" in sent[0][0][2]
    assert "127.0.0.1" not in sent[0][0][2]


def test_development_reset_link_accepts_localhost_origin(client, monkeypatch):
    sent = []
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    request_reset_link(client, base_url="http://localhost:5020")

    assert len(sent) == 1
    assert f"http://localhost:5020/reset-password/{USER_ID}#" in sent[0][0][2]


def test_development_reset_link_rejects_nonlocal_request_origin(client, monkeypatch):
    sent = []
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    request_reset_link(client, base_url="http://attacker.example:5020")

    assert sent == []


def test_mismatched_origin_cannot_change_the_reset_link_origin(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append(args) or True)

    response = request_reset_link(
        client,
        base_url="http://office-test.example:5020",
        headers={"Origin": "https://attacker.example.com"},
    )

    assert b"Password reset instructions have been sent to s***********t@example.com." in response.data
    assert len(sent) == 1
    assert f"https://app.example.test/reset-password/{USER_ID}#" in sent[0][2]
    assert "attacker.example.com" not in sent[0][2]


def test_http_request_in_production_still_uses_configured_https_origin(client, monkeypatch):
    sent = []
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append(args) or True)

    request_reset_link(
        client,
        base_url="http://office-test.example:5020",
        headers={"Host": "office-test.example:5020"},
    )

    assert len(sent) == 1
    assert f"https://app.example.test/reset-password/{USER_ID}#" in sent[0][2]


def test_production_without_canonical_origin_fails_closed(client, monkeypatch):
    sent = []
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append(args) or True)

    request_reset_link(client, base_url="https://attacker.example")

    assert sent == []


def test_unknown_user_and_invalid_saved_email_get_same_generic_response(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    unknown = request_reset_link(client, "missing-account")
    store = app.config["FIRESTORE_STORE"]
    invalid_responses = []
    for saved_email in ("not-an-email", ""):
        invalid_email_user = store.get_user(USER_ID)
        invalid_email_user["email"] = saved_email
        store.save_user(invalid_email_user)
        invalid_responses.append(request_reset_link(client))

    assert GENERIC_RESPONSE in unknown.data
    assert all(GENERIC_RESPONSE in response.data for response in invalid_responses)
    assert len(sent) == 0


def test_password_reset_email_delivery_failure_keeps_generic_response(client, monkeypatch):
    def fail_sending_email(*_args, **_kwargs):
        raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr("app.send_email", fail_sending_email)

    response = request_reset_link(client)

    assert GENERIC_RESPONSE in response.data
    assert b"saved-account@example.com" not in response.data


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("hannah.work@gmail.com", "h*********k@gmail.com"),
        ("ab@example.com", "********@example.com"),
        ("a@example.com", "********@example.com"),
    ],
)
def test_password_reset_confirmation_masks_email_local_part(email, expected):
    assert app_module.mask_password_reset_email(email) == expected


def test_unverified_mobile_does_not_block_self_service_or_admin_email_resets(client, monkeypatch):
    store = app.config["FIRESTORE_STORE"]
    user = store.get_user(USER_ID)
    user["mobile_phone_verified_at"] = None
    store.save_user(user)
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append(args) or True)

    response = request_reset_link(client)
    sign_in_as_admin(client, "http://office-test.example:5020")
    admin_response = client.post(
        f"/user-management/{USER_ID}/reset-password",
        data={"csrf_token": authenticated_form_csrf(client)},
        follow_redirects=True,
        base_url="http://office-test.example:5020",
    )

    assert b"Password reset instructions have been sent to s***********t@example.com." in response.data
    assert b"password-reset link was sent" in admin_response.data
    assert len(sent) == 2
    assert store.get_user(USER_ID)["password_reset_token_hash"]


@pytest.mark.parametrize("mobile_phone", [None, "", "not-a-phone"])
def test_email_reset_request_does_not_require_a_mobile_number(client, monkeypatch, mobile_phone):
    store = app.config["FIRESTORE_STORE"]
    user = store.get_user(USER_ID)
    user["mobile_phone"] = mobile_phone
    user["mobile_phone_verified_at"] = None
    store.save_user(user)
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append(args) or True)

    response = request_reset_link(client)

    assert b"Password reset instructions have been sent to s***********t@example.com." in response.data
    assert len(sent) == 1


def test_admin_can_resend_setup_link_for_user_with_pending_mobile_verification(client, monkeypatch):
    store = app.config["FIRESTORE_STORE"]
    user = store.get_user(USER_ID)
    user["mobile_phone_verified_at"] = None
    user["must_change_password"] = True
    store.save_user(user)
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)
    base_url = "http://office-test.example:5020"
    sign_in_as_admin(client, base_url)

    response = client.post(
        f"/user-management/{USER_ID}/reset-password",
        data={"csrf_token": authenticated_form_csrf(client, base_url)},
        follow_redirects=True,
        base_url=base_url,
    )

    assert response.status_code == 200
    assert b"password-reset link was sent" in response.data
    assert len(sent) == 1
    (recipient, _subject, body), _options = sent[0]
    assert recipient == "saved-account@example.com"
    raw_token = body.rsplit("#", 1)[1].splitlines()[0]
    client.get(f"/reset-password/{USER_ID}", base_url=base_url)
    csrf = csrf_token(client, base_url)
    valid_link = client.post(
        f"/reset-password/{USER_ID}/validate",
        json={"token": raw_token, "csrf_token": csrf},
        base_url=base_url,
    )

    assert valid_link.status_code == 200
    assert valid_link.get_json() == {"valid": True, "username": "recovery-user"}


def test_admin_entered_ten_digit_mobile_allows_reset_link_validation(client):
    store = app.config["FIRESTORE_STORE"]
    user = store.get_user(USER_ID)
    user["mobile_phone"] = "9876543210"
    user["mobile_phone_verified_at"] = "2026-10-15T00:00:00Z"
    user["mobile_phone_verified_by_admin"] = True
    user["must_change_password"] = True
    raw_token = "admin-entered-number-reset-token"
    user["password_reset_token_hash"] = hashlib.sha256(raw_token.encode()).hexdigest()
    user["password_reset_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(minutes=30)
    ).isoformat()
    store.save_user(user)
    base_url = "http://office-test.example:5020"
    client.get("/reset-password/" + USER_ID, base_url=base_url)
    csrf = csrf_token(client, base_url)

    response = client.post(
        f"/reset-password/{USER_ID}/validate",
        json={"token": raw_token, "csrf_token": csrf},
        base_url=base_url,
    )

    assert response.status_code == 200
    assert response.get_json() == {"valid": True, "username": "recovery-user"}


def test_expired_and_reused_tokens_are_rejected(client, monkeypatch):
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: True)
    request_reset_link(client)
    user = app.config["FIRESTORE_STORE"].get_user(USER_ID)
    raw_token = None
    # Retrieve the newly issued raw token from the mocked sender for this test.
    captured = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: captured.append(args) or True)
    request_reset_link(client)
    raw_token = captured[-1][2].rsplit("#", 1)[1].splitlines()[0]
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    user = app.config["FIRESTORE_STORE"].get_user(USER_ID)
    base_url = "http://office-test.example:5020"
    csrf = csrf_token(client, base_url)

    user["password_reset_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    app.config["FIRESTORE_STORE"].save_user(user)
    expired = client.post(
        f"/reset-password/{USER_ID}/validate",
        json={"token": raw_token, "csrf_token": csrf},
        base_url=base_url,
    )
    assert expired.status_code == 400
    assert "username" not in expired.get_json()

    user = app.config["FIRESTORE_STORE"].get_user(USER_ID)
    user["password_reset_token_hash"] = token_hash
    user["password_reset_expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    app.config["FIRESTORE_STORE"].save_user(user)
    valid_link = client.post(
        f"/reset-password/{USER_ID}/validate",
        json={"token": raw_token, "csrf_token": csrf},
        base_url=base_url,
    )
    assert valid_link.status_code == 200
    assert valid_link.get_json() == {"valid": True, "username": "recovery-user"}
    reset = client.post(
        f"/reset-password/{USER_ID}",
        data={"csrf_token": csrf, "token": raw_token, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
        follow_redirects=True,
        base_url=base_url,
    )
    assert b"Your password has been reset" in reset.data

    reused = client.post(
        f"/reset-password/{USER_ID}",
        data={"csrf_token": csrf, "token": raw_token, "new_password": "AnotherSecure3!", "confirm_password": "AnotherSecure3!"},
        base_url=base_url,
    )
    assert b"invalid or expired" in reused.data
    updated = app.config["FIRESTORE_STORE"].get_user(USER_ID)
    assert updated["role"] == "office_staff"
    assert updated["password_reset_token_hash"] is None
    assert updated["password_reset_expires_at"] is None


def test_initial_password_setup_uses_email_link_without_marking_phone_verified(client):
    store = app.config["FIRESTORE_STORE"]
    user = store.get_user(USER_ID)
    raw_token = "initial-setup-token"
    user.update({
        "must_change_password": True,
        "mobile_phone_verified_at": None,
        "password_reset_token_hash": hashlib.sha256(raw_token.encode()).hexdigest(),
        "password_reset_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    })
    store.save_user(user)
    base_url = "http://office-test.example:5020"
    client.get("/password-reset-request", base_url=base_url)
    csrf = csrf_token(client, base_url)

    valid_link = client.post(
        f"/reset-password/{USER_ID}/validate",
        json={"token": raw_token, "csrf_token": csrf},
        base_url=base_url,
    )
    setup_response = client.post(
        f"/reset-password/{USER_ID}",
        data={
            "csrf_token": csrf,
            "token": raw_token,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        follow_redirects=True,
        base_url=base_url,
    )

    assert valid_link.status_code == 200
    assert valid_link.get_json() == {"valid": True, "username": "recovery-user"}
    assert b"Your password has been reset" in setup_response.data
    updated = store.get_user(USER_ID)
    assert updated["mobile_phone_verified_at"] is None
    assert updated["must_change_password"] is False


def test_password_reset_enforces_policy_and_new_password_login(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)
    request_reset_link(client)
    token = sent[-1][0][2].rsplit("#", 1)[1].splitlines()[0]
    base_url = "http://office-test.example:5020"
    csrf = csrf_token(client, base_url)
    invalid = client.post(
        f"/reset-password/{USER_ID}",
        data={"csrf_token": csrf, "token": token, "new_password": "weak", "confirm_password": "weak"},
        base_url=base_url,
    )
    assert b"at least 8 characters" in invalid.data
    assert f'id="resetToken" value="{token}"'.encode() in invalid.data

    reset = client.post(
        f"/reset-password/{USER_ID}",
        data={"csrf_token": csrf, "token": token, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
        follow_redirects=True,
        base_url=base_url,
    )
    assert b"Your password has been reset" in reset.data
    assert app.config["FIRESTORE_STORE"].get_user(USER_ID)["role"] == "office_staff"

    old_login = client.post("/login", data={"username": "recovery-user", "password": OLD_PASSWORD}, follow_redirects=True, base_url=base_url)
    assert b"Invalid username or password" in old_login.data
    new_login = client.post("/login", data={"username": "recovery-user", "password": NEW_PASSWORD}, follow_redirects=False, base_url=base_url)
    assert new_login.status_code == 302
    assert new_login.headers["Location"].endswith("/")


def test_email_reset_link_alone_allows_password_change_without_phone_verification(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)
    request_reset_link(client)
    token = sent[-1][0][2].rsplit("#", 1)[1].splitlines()[0]
    base_url = "http://office-test.example:5020"
    csrf = csrf_token(client, base_url)
    store = app.config["FIRESTORE_STORE"]
    user = store.get_user(USER_ID)
    user["mobile_phone_verified_at"] = None
    store.save_user(user)
    reset = client.post(
        f"/reset-password/{USER_ID}",
        data={
            "csrf_token": csrf,
            "token": token,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        follow_redirects=True,
        base_url=base_url,
    )

    assert b"Your password has been reset" in reset.data
    updated = store.get_user(USER_ID)
    assert updated["mobile_phone_verified_at"] is None
    assert updated["password_reset_token_hash"] is None


def test_reset_request_is_rate_limited_with_generic_response(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)
    responses = [request_reset_link(client) for _ in range(app_module.PASSWORD_RESET_USER_LIMIT + 1)]

    assert all(
        b"Password reset instructions have been sent to s***********t@example.com." in response.data
        for response in responses[:app_module.PASSWORD_RESET_USER_LIMIT]
    )
    assert GENERIC_RESPONSE in responses[-1].data
    assert len(sent) == app_module.PASSWORD_RESET_USER_LIMIT


def test_admin_reset_emails_link_to_saved_address_without_changing_password(client, monkeypatch):
    store = app.config["FIRESTORE_STORE"]
    original_password_hash = store.get_user(USER_ID)["password_hash"]
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)
    sign_in_as_admin(client, "http://office-test.example:5020")

    response = client.post(
        f"/user-management/{USER_ID}/reset-password",
        data={"csrf_token": authenticated_form_csrf(client)},
        follow_redirects=True,
        base_url="http://office-test.example:5020",
    )

    user = store.get_user(USER_ID)
    assert response.status_code == 200
    assert b"A password-reset link was sent to saved-account@example.com" in response.data
    assert len(sent) == 1
    assert sent[0][0][0] == "saved-account@example.com"
    assert f"https://app.example.test/reset-password/{USER_ID}#" in sent[0][0][2]
    assert user["password_hash"] == original_password_hash
    assert user["must_change_password"] is False
    assert user["password_reset_token_hash"]
    assert store.audit_log[-1]["action"] == "password_reset"


def test_admin_reset_send_failure_preserves_password_and_clears_reset_token(client, monkeypatch):
    store = app.config["FIRESTORE_STORE"]
    original_password_hash = store.get_user(USER_ID)["password_hash"]

    def fail_delivery(*args, **kwargs):
        raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr("app.send_email", fail_delivery)
    sign_in_as_admin(client, "http://office-test.example:5020")

    response = client.post(
        f"/user-management/{USER_ID}/reset-password",
        data={"csrf_token": authenticated_form_csrf(client)},
        follow_redirects=True,
        base_url="http://office-test.example:5020",
    )

    user = store.get_user(USER_ID)
    assert response.status_code == 200
    assert b"Unable to send the password-reset email" in response.data
    assert user["password_hash"] == original_password_hash
    assert user["password_reset_token_hash"] is None
    assert user["password_reset_expires_at"] is None


def test_admin_reset_user_password_page_uses_the_same_email_flow(client, monkeypatch):
    store = app.config["FIRESTORE_STORE"]
    original_password_hash = store.get_user(USER_ID)["password_hash"]
    sent = []
    monkeypatch.setattr("app.send_email", lambda *args, **kwargs: sent.append((args, kwargs)) or True)
    sign_in_as_admin(client, "http://office-test.example:5020")

    response = client.post(
        "/forgot-password",
        data={"user_id": USER_ID, "csrf_token": authenticated_form_csrf(client)},
        follow_redirects=True,
        base_url="http://office-test.example:5020",
    )

    user = store.get_user(USER_ID)
    assert response.status_code == 200
    assert b"A password-reset link was sent to saved-account@example.com" in response.data
    assert len(sent) == 1
    assert user["password_hash"] == original_password_hash
    assert user["password_reset_token_hash"]
