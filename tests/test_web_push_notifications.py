import base64
import uuid
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from app import InMemoryFirestoreStore, app


USER_ID = "push-admin"
SESSION_ID = "push-session"
CSRF = "profile-push-csrf"
RED_BOOK_CSRF = "red-book-push-csrf"


class FakeFirebaseMessaging:
    class MulticastMessage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    @staticmethod
    def WebpushConfig(**kwargs):
        return kwargs

    def __init__(self, responses=None, error=None):
        self.messages = []
        self.responses = responses
        self.error = error

    def send_each_for_multicast(self, message):
        self.messages.append(message)
        if self.error:
            raise self.error
        responses = self.responses or [
            SimpleNamespace(success=True, exception=None)
            for _token in message.tokens
        ]
        return SimpleNamespace(
            responses=responses,
            failure_count=sum(not result.success for result in responses),
        )


@pytest.fixture
def push_client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.save_user({
        "id": USER_ID,
        "username": "push-admin",
        "role": "super_admin",
        "status": "active",
        "is_active": True,
        "must_change_password": False,
        "active_sessions": [SESSION_ID],
        "authorization_version": 1,
        "password_hash": generate_password_hash("PushTestSecure1!"),
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "web-push-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    monkeypatch.setitem(app.config, "FIREBASE_WEB_PUSH_VAPID_KEY", "")
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["session_id"] = SESSION_ID
        flask_session["profile_avatar_csrf"] = CSRF
        flask_session["red_book_csrf"] = RED_BOOK_CSRF
    return store, client


def add_red_book_item(store, text="Check reinforcement before concrete placement."):
    item_id = str(uuid.uuid4())
    store.create_red_book_item(item_id, text, USER_ID)
    return item_id


def pin(client, item_id, pinned=True):
    return client.put(
        f"/api/red-book/items/{item_id}/pin",
        json={"pinned": pinned},
        headers={"X-CSRF-Token": RED_BOOK_CSRF},
    )


def add_device_token(store, user_id, token):
    store.save_fcm_device_token(app_module._fcm_device_token_id(token), user_id, token)


def test_new_pin_sends_once_to_active_red_book_readers_only(push_client, monkeypatch):
    store, client = push_client
    reader = store.save_user({
        "id": "site-reader",
        "username": "site-reader",
        "role": "Site Reader",
        "custom_staff_type": "Site Reader",
        "status": "active",
        "is_active": True,
        "must_change_password": False,
        "permissions": [],
    })
    store.save_custom_staff_type({
        "id": "site-reader-role",
        "name": "Site Reader",
        "permissions": ["red_book.read"],
    })
    no_read_user = store.save_user({
        "id": "no-red-book-access",
        "username": "no-red-book-access",
        "role": "No Red Book",
        "custom_staff_type": "No Red Book",
        "status": "active",
        "is_active": True,
        "permissions": [],
    })
    store.save_custom_staff_type({
        "id": "no-red-book-role",
        "name": "No Red Book",
        "permissions": ["dashboard"],
    })
    inactive_user = store.save_user({
        "id": "inactive-reader",
        "username": "inactive-reader",
        "role": "super_admin",
        "status": "inactive",
        "is_active": False,
        "permissions": [],
    })
    setup_user = store.save_user({
        "id": "setup-reader",
        "username": "setup-reader",
        "role": "super_admin",
        "status": "active",
        "is_active": True,
        "must_change_password": True,
        "permissions": [],
    })
    reader_token = "reader-device-token-" + "a" * 45
    second_reader_token = "second-reader-device-token-" + "j" * 45
    no_read_token = "unauthorized-device-token-" + "b" * 45
    inactive_token = "inactive-device-token-" + "h" * 45
    setup_token = "setup-device-token-" + "i" * 45
    add_device_token(store, reader["id"], reader_token)
    add_device_token(store, reader["id"], second_reader_token)
    add_device_token(store, no_read_user["id"], no_read_token)
    add_device_token(store, inactive_user["id"], inactive_token)
    add_device_token(store, setup_user["id"], setup_token)
    messaging = FakeFirebaseMessaging()
    monkeypatch.setattr(app_module, "firebase_messaging", messaging)
    item_text = "Check reinforcement before concrete placement."
    item_id = add_red_book_item(store, item_text)

    first = pin(client, item_id)
    repeated = pin(client, item_id)

    assert first.status_code == repeated.status_code == 200
    assert first.get_json()["changed"] is True
    assert repeated.get_json()["changed"] is False
    assert len(messaging.messages) == 1
    message = messaging.messages[0]
    assert message.tokens == [reader_token, second_reader_token]
    assert message.data["title"] == "Mannat Moon Construction — Red Book"
    assert message.data["body"] == f"A Red Book item has been pinned: {item_text}"
    assert message.data["url"] == f"/red-book#red-book-item-{item_id}"
    assert message.data["event_id"]


def test_unpin_does_not_notify_but_repin_is_a_new_event(push_client, monkeypatch):
    store, client = push_client
    token = "authorized-device-token-" + "c" * 45
    add_device_token(store, USER_ID, token)
    messaging = FakeFirebaseMessaging()
    monkeypatch.setattr(app_module, "firebase_messaging", messaging)
    item_id = add_red_book_item(store)

    no_op_unpin = pin(client, item_id, False)
    assert no_op_unpin.status_code == 200
    assert no_op_unpin.get_json()["changed"] is False
    assert messaging.messages == []
    assert pin(client, item_id, True).get_json()["changed"] is True
    unpin_response = pin(client, item_id, False)
    assert unpin_response.status_code == 200
    assert unpin_response.get_json()["changed"] is True
    assert len(messaging.messages) == 1
    assert pin(client, item_id, True).get_json()["changed"] is True
    assert len(messaging.messages) == 2


def test_edits_and_reads_do_not_send_pin_notifications(push_client, monkeypatch):
    store, client = push_client
    send_calls = []
    monkeypatch.setattr(
        app_module,
        "_send_red_book_pin_notification",
        lambda *args: send_calls.append(args),
    )
    item_id = add_red_book_item(store)

    assert client.get("/api/red-book/items").status_code == 200
    edited = client.patch(
        f"/api/red-book/items/{item_id}",
        json={"text": "Revised construction instruction."},
        headers={"X-CSRF-Token": RED_BOOK_CSRF},
    )

    assert edited.status_code == 200
    assert send_calls == []


def test_fcm_delivery_exception_does_not_undo_successful_pin(push_client, monkeypatch):
    store, client = push_client
    add_device_token(store, USER_ID, "failing-device-token-" + "d" * 45)
    monkeypatch.setattr(
        app_module,
        "firebase_messaging",
        FakeFirebaseMessaging(error=RuntimeError("simulated delivery outage")),
    )
    item_id = add_red_book_item(store)

    response = pin(client, item_id)

    assert response.status_code == 200
    assert response.get_json()["pinned"] is True
    assert store.red_book_items[item_id]["pinned"] is True


def test_invalid_fcm_token_is_removed_after_delivery_failure(push_client, monkeypatch):
    store, client = push_client
    token = "expired-device-token-" + "e" * 45
    valid_token = "valid-device-token-" + "k" * 45
    token_id = app_module._fcm_device_token_id(token)
    add_device_token(store, USER_ID, token)
    add_device_token(store, USER_ID, valid_token)
    messaging = FakeFirebaseMessaging(responses=[
        SimpleNamespace(
            success=False,
            exception=SimpleNamespace(code="messaging/registration-token-not-registered"),
        ),
        SimpleNamespace(success=True, exception=None),
    ])
    monkeypatch.setattr(app_module, "firebase_messaging", messaging)
    item_id = add_red_book_item(store)

    response = pin(client, item_id)

    assert response.status_code == 200
    assert token_id not in store.fcm_device_tokens


def test_fcm_delivery_failures_log_codes_without_device_tokens(push_client, monkeypatch, caplog):
    store, client = push_client
    token = "failing-device-token-" + "d" * 45
    add_device_token(store, USER_ID, token)
    monkeypatch.setattr(
        app_module,
        "firebase_messaging",
        FakeFirebaseMessaging(responses=[
            SimpleNamespace(
                success=False,
                exception=SimpleNamespace(
                    code="messaging/sender-id-mismatch",
                    token=token,
                ),
            ),
        ]),
    )
    item_id = add_red_book_item(store)

    with caplog.at_level("WARNING", logger=app_module.app.logger.name):
        response = pin(client, item_id)

    assert response.status_code == 200
    assert "messaging/sender-id-mismatch: 1" in caplog.text
    assert token not in caplog.text


def test_missing_push_configuration_is_reported_without_breaking_pin(push_client, monkeypatch):
    store, client = push_client
    monkeypatch.setattr(app_module, "firebase_messaging", FakeFirebaseMessaging())
    monkeypatch.setattr(
        app_module,
        "get_active_firebase_config",
        lambda: {"web_config": {"messagingSenderId": "123"}},
    )
    config = client.get("/api/notifications/config")
    item_id = add_red_book_item(store)

    response = pin(client, item_id)

    assert config.status_code == 200
    assert config.get_json()["configured"] is False
    assert response.status_code == 200
    assert store.red_book_items[item_id]["pinned"] is True


def test_authenticated_device_registration_is_bound_to_current_user(push_client):
    store, client = push_client
    token = "browser-device-token-" + "f" * 45
    headers = {"X-CSRF-Token": CSRF}

    registered = client.post(
        "/api/notifications/device",
        json={"token": token},
        headers=headers,
    )
    status = client.post(
        "/api/notifications/device/status",
        json={"token": token},
        headers=headers,
    )
    forged = client.post(
        "/api/notifications/device",
        json={"token": token, "user_id": "another-user"},
        headers=headers,
    )
    unregistered = client.delete(
        "/api/notifications/device",
        json={"token": token},
        headers=headers,
    )

    assert registered.status_code == 201
    assert registered.get_json() == {"registered": True}
    assert status.get_json() == {"registered": True}
    assert forged.status_code == 400
    assert unregistered.status_code == 200
    assert store.fcm_device_tokens == {}


def test_unauthenticated_user_cannot_register_a_token(push_client):
    _store, authenticated_client = push_client
    token = "browser-device-token-" + "g" * 45
    unauthenticated_client = app.test_client()

    response = unauthenticated_client.post(
        "/api/notifications/device",
        json={"token": token},
        headers={"X-CSRF-Token": CSRF},
    )

    assert response.status_code == 401


def test_user_cannot_remove_another_users_device_token(push_client):
    store, client = push_client
    other_user_id = "another-device-owner"
    token = "another-users-device-token-" + "l" * 45
    add_device_token(store, other_user_id, token)

    response = client.delete(
        "/api/notifications/device",
        json={"token": token},
        headers={"X-CSRF-Token": CSRF},
    )

    assert response.status_code == 200
    assert app_module._fcm_device_token_id(token) in store.fcm_device_tokens
    assert store.fcm_device_tokens[app_module._fcm_device_token_id(token)]["user_id"] == other_user_id


def test_service_worker_uses_existing_public_firebase_project(push_client, monkeypatch):
    _store, client = push_client
    monkeypatch.setattr(
        app_module,
        "get_active_firebase_config",
        lambda: {
            "web_config": {
                "apiKey": "public-test-config",
                "projectId": "mannat-moon-construction",
                "messagingSenderId": "123",
            }
        },
    )

    response = client.get("/firebase-messaging-sw.js")

    assert response.status_code == 200
    assert response.headers["Service-Worker-Allowed"] == "/"
    assert "self.MANNAT_FIREBASE_CONFIG" in response.get_data(as_text=True)
    assert "private_key" not in response.get_data(as_text=True)


def test_valid_vapid_public_key_is_recognized():
    key = base64.urlsafe_b64encode(b"\x04" + b"x" * 64).decode("ascii").rstrip("=")

    assert app_module._valid_firebase_vapid_public_key(key)
    assert app_module._valid_firebase_vapid_public_key(key + "=")
    assert not app_module._valid_firebase_vapid_public_key("not-a-vapid-key")
