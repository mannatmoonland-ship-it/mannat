import re
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as app_module


@pytest.fixture
def profile_client(monkeypatch):
    store = app_module.InMemoryFirestoreStore()
    store.save_user({
        "id": "profile-user",
        "username": "builder",
        "full_name": "Casey Builder",
        "email": "casey@example.com",
        "recovery_email": "casey.recovery@example.com",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": generate_password_hash("Example1!Password"),
        "must_change_password": False,
        "status": "active",
        "active_sessions": ["profile-session"],
        "created_at": "2025-01-10T00:00:00Z",
        "last_login_at": "2026-09-25T22:57:25Z",
    })
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    monkeypatch.setitem(app_module.app.config, "SECRET_KEY", "profile-test-secret")
    monkeypatch.setitem(app_module.app.config, "FIRESTORE_STORE", store)
    monkeypatch.setattr(app_module, "SMS_OTP_ATTEMPTS", {})
    monkeypatch.setattr(app_module, "_send_fast2sms_otp", lambda *_args: None)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "profile-user"
        session["session_id"] = "profile-session"
    return client, store


def csrf_token_from(response):
    match = re.search(r'data-csrf-token="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def logout_with_csrf(client):
    client.get("/profile-settings")
    with client.session_transaction() as session:
        token = session["authenticated_form_csrf"]
    return client.post("/logout", data={"csrf_token": token})


def save_profile(
    client,
    csrf_token,
    *,
    full_name="Casey Builder",
    email="casey@example.com",
    recovery_email="casey.recovery@example.com",
    **extra,
):
    payload = {
        "full_name": full_name,
        "email": email,
        "recovery_email": recovery_email,
        **extra,
    }
    return client.patch(
        "/api/profile",
        json=payload,
        headers={"X-CSRF-Token": csrf_token},
    )


def test_profile_settings_shows_safe_authenticated_user_information(profile_client):
    client, _ = profile_client

    response = client.get("/profile-settings")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Casey Builder" in html
    assert "@builder" in html
    assert "casey@example.com" in html
    assert "casey.recovery@example.com" in html
    assert "Recovery Email" in html
    assert "Office Staff" in html
    assert "active" in html
    assert 'datetime="2026-09-25T22:57:25Z"' in html
    assert 'data-profile-timestamp' in html
    assert '2026-09-25T22:57:25Z</time>' in html
    profile_script = Path(app_module.app.root_path, "static", "profile_settings.js").read_text(encoding="utf-8")
    assert 'import { formatDate, formatDateTime } from "./date_format.js";' in profile_script
    assert "formatDateTime(value)" in profile_script
    assert "password_hash" not in html
    assert "Example1!Password" not in html
    assert "profile_avatar_csrf" not in html


def test_profile_fields_are_prefilled_and_controlled_fields_remain_read_only(profile_client):
    client, _ = profile_client

    response = client.get("/profile-settings")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="profile-full-name"' in html
    assert 'value="Casey Builder"' in html
    assert 'id="profile-email"' in html
    assert 'value="casey@example.com"' in html
    assert 'id="profile-recovery-email"' in html
    assert 'value="casey.recovery@example.com"' in html
    assert "Primary / Account Email" in html
    assert "Recovery Email" in html
    assert "Save Changes" in html
    assert "Username" in html and "@builder" in html
    assert "Office Staff" in html
    assert 'name="username"' not in html
    assert 'name="role"' not in html
    assert 'name="status"' not in html


def test_profile_update_trims_and_persists_profile_emails(profile_client):
    client, store = profile_client
    csrf_token = csrf_token_from(client.get("/profile-settings"))
    original = store.get_user("profile-user")

    response = save_profile(
        client,
        csrf_token,
        full_name="  Casey   Moon  ",
        email="  casey.moon@example.com  ",
        recovery_email="  casey.recovery.moon@example.com  ",
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "full_name": "Casey Moon",
        "email": "casey.moon@example.com",
        "recovery_email": "casey.recovery.moon@example.com",
    }
    saved = store.get_user("profile-user")
    assert saved["full_name"] == "Casey Moon"
    assert saved["email"] == "casey.moon@example.com"
    assert saved["recovery_email"] == "casey.recovery.moon@example.com"
    assert saved["username"] == "builder"
    assert saved["role"] == "office_staff"
    assert saved["status"] == original["status"]
    assert saved["password_hash"] == original["password_hash"]
    assert saved["avatarId"] == original["avatarId"]

    refreshed = client.get("/profile-settings").get_data(as_text=True)
    assert 'value="Casey Moon"' in refreshed
    assert 'value="casey.moon@example.com"' in refreshed
    assert 'value="casey.recovery.moon@example.com"' in refreshed
    assert "@builder" in refreshed


def test_profile_update_rejects_blank_name_and_invalid_emails(profile_client):
    client, store = profile_client
    csrf_token = csrf_token_from(client.get("/profile-settings"))

    blank_name = save_profile(client, csrf_token, full_name=" \n\t ", email="valid@example.com")
    invalid_email = save_profile(client, csrf_token, email="not-an-email")
    invalid_recovery_email = save_profile(
        client,
        csrf_token,
        recovery_email="not-an-email",
    )

    assert blank_name.status_code == 400
    assert "cannot be blank" in blank_name.get_json()["error"]
    assert invalid_email.status_code == 400
    assert "valid account email" in invalid_email.get_json()["error"]
    assert invalid_recovery_email.status_code == 400
    assert "valid recovery email" in invalid_recovery_email.get_json()["error"]
    assert store.get_user("profile-user")["full_name"] == "Casey Builder"
    assert store.get_user("profile-user")["email"] == "casey@example.com"
    assert store.get_user("profile-user")["recovery_email"] == "casey.recovery@example.com"


def test_profile_update_rejects_attempts_to_change_controlled_fields(profile_client):
    client, store = profile_client
    csrf_token = csrf_token_from(client.get("/profile-settings"))

    response = save_profile(
        client,
        csrf_token,
        username="changed",
        role="super_admin",
    )

    assert response.status_code == 400
    saved = store.get_user("profile-user")
    assert saved["username"] == "builder"
    assert saved["role"] == "office_staff"


def test_profile_update_requires_csrf_and_authentication(profile_client):
    client, _ = profile_client

    missing_csrf = client.patch(
        "/api/profile",
        json={
            "full_name": "Casey Moon",
            "email": "casey@example.com",
            "recovery_email": "casey.recovery@example.com",
        },
    )
    assert missing_csrf.status_code == 403

    csrf_token = csrf_token_from(client.get("/profile-settings"))
    logout_with_csrf(client)
    logged_out = save_profile(client, csrf_token, full_name="Casey Moon")
    assert logged_out.status_code == 401


def test_profile_mobile_phone_requires_csrf_and_sms_code(profile_client, monkeypatch):
    client, store = profile_client
    page = client.get("/profile-settings")
    csrf_token = csrf_token_from(page)

    missing_csrf = client.post(
        "/api/profile/mobile-phone/send-code",
        json={
            "mobile_phone": "9876543210",
        },
    )
    sent_codes = []
    monkeypatch.setattr(app_module, "_send_fast2sms_otp", lambda _phone, code: sent_codes.append(code))
    sent = client.post(
        "/api/profile/mobile-phone/send-code",
        json={"mobile_phone": "9876543210"},
        headers={"X-CSRF-Token": csrf_token},
    )
    stored_challenge = next(iter(store.sms_otp_challenges.values()))
    assert stored_challenge["code_digest"] == app_module._sms_otp_digest(
        stored_challenge["id"], sent_codes[-1]
    )
    assert sent_codes[-1] not in stored_challenge.values()
    wrong_code = client.post(
        "/api/profile/mobile-phone",
        json={
            "mobile_phone": "9876543210",
            "code": "000000" if sent_codes[-1] != "000000" else "000001",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    saved = client.post(
        "/api/profile/mobile-phone",
        json={
            "mobile_phone": "9876543210",
            "code": sent_codes[-1],
        },
        headers={"X-CSRF-Token": csrf_token},
    )

    assert missing_csrf.status_code == 403
    assert sent.status_code == 200
    assert wrong_code.status_code == 400
    assert saved.status_code == 200
    assert saved.get_json() == {
        "mobile_phone": "9876543210",
        "mobile_phone_verified": True,
    }
    user = store.get_user("profile-user")
    assert user["mobile_phone"] == "9876543210"
    assert user["mobile_phone_verified_at"]


def test_profile_mobile_phone_rejects_invalid_number_and_incorrect_code(profile_client, monkeypatch):
    client, store = profile_client
    csrf_token = csrf_token_from(client.get("/profile-settings"))

    invalid_number = client.post(
        "/api/profile/mobile-phone/send-code",
        json={
            "mobile_phone": "4155550123",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    non_indian_number = client.post(
        "/api/profile/mobile-phone/send-code",
        json={"mobile_phone": "+14155550123"},
        headers={"X-CSRF-Token": csrf_token},
    )
    wrong_code = client.post(
        "/api/profile/mobile-phone",
        json={
            "mobile_phone": "9876543210",
            "code": "000000",
        },
        headers={"X-CSRF-Token": csrf_token},
    )

    assert invalid_number.status_code == 400
    assert non_indian_number.status_code == 400
    assert wrong_code.status_code == 400
    assert store.get_user("profile-user").get("mobile_phone") is None


def test_profile_mobile_otp_is_invalidated_after_too_many_attempts(profile_client, monkeypatch):
    client, store = profile_client
    csrf_token = csrf_token_from(client.get("/profile-settings"))
    sent_codes = []
    monkeypatch.setattr(
        app_module, "_send_fast2sms_otp", lambda _phone, code: sent_codes.append(code)
    )
    sent = client.post(
        "/api/profile/mobile-phone/send-code",
        json={"mobile_phone": "9876543210"},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert sent.status_code == 200
    for _ in range(app_module.SMS_OTP_MAX_VERIFY_ATTEMPTS):
        wrong_code = client.post(
            "/api/profile/mobile-phone",
            json={
                "mobile_phone": "9876543210",
                "code": "000000" if sent_codes[-1] != "000000" else "000001",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert wrong_code.status_code == 400

    assert store.sms_otp_challenges == {}
    correct_code_after_lockout = client.post(
        "/api/profile/mobile-phone",
        json={"mobile_phone": "9876543210", "code": sent_codes[-1]},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert correct_code_after_lockout.status_code == 400
    assert store.get_user("profile-user").get("mobile_phone") is None


def test_profile_update_reports_storage_errors(profile_client, monkeypatch):
    client, store = profile_client
    csrf_token = csrf_token_from(client.get("/profile-settings"))

    def fail_update(*_args):
        raise RuntimeError("Firestore unavailable")

    monkeypatch.setattr(store, "update_user_profile", fail_update)
    response = save_profile(client, csrf_token, full_name="Casey Moon")

    assert response.status_code == 503
    assert response.get_json() == {"error": "Profile updates are temporarily unavailable."}


def test_profile_update_persists_after_logout_and_login(profile_client):
    client, store = profile_client
    csrf_token = csrf_token_from(client.get("/profile-settings"))

    saved = save_profile(
        client,
        csrf_token,
        full_name="Casey Moon",
        email="moon@example.com",
        recovery_email="moon.recovery@example.com",
    )
    assert saved.status_code == 200
    logout_with_csrf(client)

    login = client.post(
        "/login",
        data={"username": "builder", "password": "Example1!Password"},
        follow_redirects=True,
    )

    assert login.status_code == 200
    assert store.get_user("profile-user")["full_name"] == "Casey Moon"
    assert store.get_user("profile-user")["email"] == "moon@example.com"
    assert store.get_user("profile-user")["recovery_email"] == "moon.recovery@example.com"
    profile = client.get("/profile-settings").get_data(as_text=True)
    assert 'value="Casey Moon"' in profile
    assert 'value="moon@example.com"' in profile
    assert 'value="moon.recovery@example.com"' in profile


def test_avatar_is_saved_to_the_authenticated_users_document_and_survives_reload(profile_client):
    client, store = profile_client
    page = client.get("/profile-settings")
    csrf_token = csrf_token_from(page)

    response = client.post(
        "/api/profile/avatar",
        json={"avatarId": "panda", "userId": "another-user"},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    assert response.get_json() == {"avatarId": "panda"}
    assert store.get_user("profile-user")["avatarId"] == "panda"
    assert store.get_user("another-user") is None

    refreshed = client.get("/profile-settings")
    assert 'data-avatar-id="panda"' in refreshed.get_data(as_text=True)


def test_avatar_survives_logout_and_login(profile_client):
    client, store = profile_client
    csrf_token = csrf_token_from(client.get("/profile-settings"))
    saved = client.post(
        "/api/profile/avatar",
        json={"avatarId": "unicorn"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert saved.status_code == 200
    logout_with_csrf(client)

    login = client.post(
        "/login",
        data={"username": "builder", "password": "Example1!Password"},
        follow_redirects=True,
    )

    assert login.status_code == 200
    assert store.get_user("profile-user")["avatarId"] == "unicorn"
    assert 'data-avatar-id="unicorn"' in client.get("/profile-settings").get_data(as_text=True)


def test_avatar_endpoint_rejects_missing_csrf_and_unknown_avatar(profile_client):
    client, store = profile_client
    page = client.get("/profile-settings")
    csrf_token = csrf_token_from(page)

    no_token = client.post("/api/profile/avatar", json={"avatarId": "bear"})
    unknown = client.post(
        "/api/profile/avatar",
        json={"avatarId": "not-in-the-library"},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert no_token.status_code == 403
    assert unknown.status_code == 400
    assert store.get_user("profile-user")["avatarId"] == "cute_cat"


def test_dashboard_profile_link_uses_the_saved_animal_avatar(profile_client):
    client, _ = profile_client

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/profile-settings"' in html
    assert 'data-animal-avatar="cute_cat"' in html
    assert "renderAnimalAvatar" in html


def test_avatar_library_contains_the_supported_animals():
    assert len(app_module.PROFILE_AVATAR_IDS) >= 30
    assert {
        "cute_cat", "puppy", "panda", "rabbit", "fox", "lion", "tiger",
        "elephant", "monkey", "owl", "chicken", "cow", "whale", "dino",
        "unicorn", "dragon",
    }.issubset(app_module.PROFILE_AVATAR_IDS)

    avatar_script = Path(app_module.app.root_path, "static", "animal_avatars.js").read_text(encoding="utf-8")
    script_ids = set(re.findall(r'\bid:\s*"([^"]+)"', avatar_script))
    assert script_ids == app_module.PROFILE_AVATAR_IDS