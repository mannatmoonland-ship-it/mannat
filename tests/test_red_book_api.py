import uuid
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from app import InMemoryFirestoreStore, app


CSRF = "red-book-csrf-test-token"
SESSION_ID = "red-book-test-session"
USER_ID = "red-book-test-user"


@pytest.fixture
def red_book_client(monkeypatch):
    store = InMemoryFirestoreStore()
    user = store.save_user({
        "id": USER_ID,
        "username": "red-book-user",
        "role": "super_admin",
        "custom_staff_type": "",
        "status": "active",
        "is_active": True,
        "active_sessions": [SESSION_ID],
        "authorization_version": 1,
        "password_hash": generate_password_hash("CurrentSecure1!"),
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "red-book-api-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user["id"]
        flask_session["session_id"] = SESSION_ID
        flask_session["red_book_csrf"] = CSRF
    return store, client


def headers(csrf=CSRF):
    return {} if csrf is None else {"X-CSRF-Token": csrf}


def add_item(client, text):
    response = client.post(
        "/api/red-book/items",
        json={"text": text},
        headers=headers(),
    )
    assert response.status_code == 201
    return response.get_json()["id"]


def set_role(store, client, role, permissions=None):
    user = store.get_user(USER_ID)
    user["role"] = role
    user["custom_staff_type"] = (
        role if role not in {"super_admin", "office_staff", "supervisor"} else ""
    )
    store.save_user(user)
    if permissions is not None:
        store.save_custom_staff_type({
            "id": "red-book-custom-role",
            "name": role,
            "permissions": permissions,
        })
    with client.session_transaction() as flask_session:
        flask_session["role"] = role
        flask_session["permissions"] = list(permissions or [])


def test_red_book_create_edit_delete_and_audit(red_book_client):
    store, client = red_book_client
    item_id = add_item(client, "  Check excavation depth against the approved drawing.  ")

    created = client.get("/api/red-book/items").get_json()["items"]
    assert created[0]["id"] == item_id
    assert created[0]["text"] == "Check excavation depth against the approved drawing."
    assert created[0]["sort_order"] == 0
    assert "created_by" not in created[0]
    assert store.red_book_items[item_id]["created_by"] == USER_ID

    updated = client.patch(
        f"/api/red-book/items/{item_id}",
        json={"text": "Verify footing dimensions before placing PCC."},
        headers=headers(),
    )
    assert updated.status_code == 200
    saved = client.get("/api/red-book/items").get_json()["items"][0]
    assert saved["text"] == "Verify footing dimensions before placing PCC."
    assert store.red_book_items[item_id]["updated_by"] == USER_ID

    deleted = client.delete(f"/api/red-book/items/{item_id}", headers=headers())
    assert deleted.status_code == 200
    assert client.get("/api/red-book/items").get_json()["items"] == []


def test_red_book_preserves_multiline_instruction_text(red_book_client):
    _store, client = red_book_client
    text = "Check excavation depth.\nConfirm dimensions before placing PCC."
    item_id = add_item(client, text)

    updated_text = "Check excavation depth.\n\nRecord measurements in the site log."
    updated = client.patch(
        f"/api/red-book/items/{item_id}",
        json={"text": updated_text},
        headers=headers(),
    )

    assert updated.status_code == 200
    saved = client.get("/api/red-book/items").get_json()["items"][0]
    assert saved["text"] == updated_text


def test_red_book_image_link_can_be_added_updated_and_removed(red_book_client):
    _store, client = red_book_client
    image_url = "https://drive.google.com/file/d/example-image-id/view?usp=sharing"
    response = client.post(
        "/api/red-book/items",
        json={"text": "Inspect the retaining wall.", "image_url": image_url},
        headers=headers(),
    )
    assert response.status_code == 201
    item_id = response.get_json()["id"]
    saved = client.get("/api/red-book/items").get_json()["items"][0]
    assert saved["image_url"] == image_url

    updated_url = "https://drive.google.com/open?id=updated-image-id"
    updated = client.patch(
        f"/api/red-book/items/{item_id}",
        json={"text": "Inspect the retaining wall.", "image_url": updated_url},
        headers=headers(),
    )
    assert updated.status_code == 200
    saved = client.get("/api/red-book/items").get_json()["items"][0]
    assert saved["image_url"] == updated_url

    removed = client.patch(
        f"/api/red-book/items/{item_id}",
        json={"text": "Inspect the retaining wall.", "image_url": ""},
        headers=headers(),
    )
    assert removed.status_code == 200
    saved = client.get("/api/red-book/items").get_json()["items"][0]
    assert saved["image_url"] == ""


def test_red_book_text_only_edit_preserves_existing_image_link(red_book_client):
    _store, client = red_book_client
    image_url = "https://drive.google.com/file/d/example-image-id/view"
    response = client.post(
        "/api/red-book/items",
        json={"text": "Inspect the retaining wall.", "image_url": image_url},
        headers=headers(),
    )
    item_id = response.get_json()["id"]

    updated = client.patch(
        f"/api/red-book/items/{item_id}",
        json={"text": "Inspect the completed retaining wall."},
        headers=headers(),
    )

    assert updated.status_code == 200
    saved = client.get("/api/red-book/items").get_json()["items"][0]
    assert saved["image_url"] == image_url
    assert saved["text"] == "Inspect the completed retaining wall."


@pytest.mark.parametrize(
    "image_url",
    [
        "http://drive.google.com/file/d/example-image-id/view",
        "https://drive.google.com.evil.example/file/d/example-image-id/view",
        "https://example.com/file/d/example-image-id/view",
        "https://drive.google.com/",
    ],
)
def test_red_book_rejects_non_drive_image_links(red_book_client, image_url):
    _store, client = red_book_client
    response = client.post(
        "/api/red-book/items",
        json={"text": "Inspect the retaining wall.", "image_url": image_url},
        headers=headers(),
    )

    assert response.status_code == 400
    assert client.get("/api/red-book/items").get_json()["items"] == []


def test_red_book_order_is_saved_and_read_in_priority_order(red_book_client):
    _store, client = red_book_client
    first_id = add_item(client, "First instruction.")
    second_id = add_item(client, "Second instruction.")
    third_id = add_item(client, "Third instruction.")

    reordered = client.post(
        "/api/red-book/order",
        json={"ids": [third_id, first_id, second_id]},
        headers=headers(),
    )
    assert reordered.status_code == 200

    refreshed = app.test_client()
    with refreshed.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["session_id"] = SESSION_ID
        flask_session["red_book_csrf"] = CSRF
    items = refreshed.get("/api/red-book/items").get_json()["items"]
    assert [item["id"] for item in items] == [third_id, first_id, second_id]
    assert [item["sort_order"] for item in items] == [0, 1, 2]


def test_red_book_delete_renumbers_remaining_items(red_book_client):
    _store, client = red_book_client
    first_id = add_item(client, "First instruction.")
    second_id = add_item(client, "Second instruction.")
    third_id = add_item(client, "Third instruction.")

    response = client.delete(f"/api/red-book/items/{second_id}", headers=headers())

    assert response.status_code == 200
    items = client.get("/api/red-book/items").get_json()["items"]
    assert [item["id"] for item in items] == [first_id, third_id]
    assert [item["sort_order"] for item in items] == [0, 1]


@pytest.mark.parametrize("text", ["", "   ", "one\tinstruction", "x" * 501, None, 12])
def test_red_book_rejects_invalid_instruction_text(red_book_client, text):
    _store, client = red_book_client
    response = client.post(
        "/api/red-book/items",
        json={"text": text},
        headers=headers(),
    )
    assert response.status_code == 400
    assert client.get("/api/red-book/items").get_json()["items"] == []


def test_red_book_rejects_stale_order_without_changing_data(red_book_client):
    store, client = red_book_client
    item_id = add_item(client, "Keep this instruction.")

    response = client.post(
        "/api/red-book/order",
        json={"ids": [str(uuid.uuid4())]},
        headers=headers(),
    )

    assert response.status_code == 409
    assert list(store.red_book_items) == [item_id]


def test_red_book_requires_authentication_and_csrf(red_book_client):
    _store, client = red_book_client
    with client.session_transaction() as flask_session:
        flask_session.clear()
    assert client.get("/api/red-book/items").status_code == 401
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["session_id"] = SESSION_ID
    response = client.post("/api/red-book/items", json={"text": "Do a safety check."})
    assert response.status_code == 403


def test_operational_roles_can_manage_but_cannot_delete(red_book_client):
    store, client = red_book_client
    set_role(store, client, "office_staff")
    page = client.get("/red-book")
    item_id = add_item(client, "Check reinforcement spacing before concreting.")

    edited = client.patch(
        f"/api/red-book/items/{item_id}",
        json={"text": "Check the reinforcement spacing before concreting."},
        headers=headers(),
    )
    deleted = client.delete(f"/api/red-book/items/{item_id}", headers=headers())

    assert edited.status_code == 200
    assert deleted.status_code == 403
    assert item_id in store.red_book_items
    assert b'data-can-edit="true"' in page.data
    assert b'data-can-delete="false"' in page.data


def test_custom_roles_require_explicit_red_book_permissions(red_book_client):
    store, client = red_book_client
    set_role(store, client, "Site Engineer", [])
    denied_read = client.get("/api/red-book/items")
    denied_create = client.post(
        "/api/red-book/items",
        json={"text": "Check the concrete grade."},
        headers=headers(),
    )
    assert denied_read.status_code == 403
    assert denied_create.status_code == 403

    set_role(
        store,
        client,
        "Site Engineer",
        ["red_book.read", "red_book.create", "red_book.edit"],
    )
    assert client.get("/api/red-book/items").status_code == 200
    assert add_item(client, "Check the concrete grade.")


def test_red_book_page_includes_construction_navigation_and_controls(red_book_client):
    _store, client = red_book_client
    response = client.get("/red-book")

    assert response.status_code == 200
    assert b"Red Book" in response.data
    assert b"Save Order" not in response.data
    assert b"redBookAddForm" in response.data
    assert b"Add image (Google Drive link)" in response.data


def test_red_book_policy_and_delete_confirmation_are_explicit():
    source = (Path(__file__).parents[1] / "permission_policy.py").read_text(
        encoding="utf-8"
    )
    assert '"red_book.read"' in source
    assert '"red_book.create"' in source
    assert '"red_book.edit"' in source
    assert '"red_book.delete"' in source
    javascript = (Path(__file__).parents[1] / "static" / "red_book.js").read_text(
        encoding="utf-8"
    )
    assert 'window.confirm("Delete this Red Book item?")' in javascript
    assert 'actionButton("↑"' in javascript
    assert 'actionButton("↓"' in javascript
    assert 'orderIsSaving || index === 0, "Move Up"' in javascript
    assert 'orderIsSaving || index === items.length - 1, "Move Down"' in javascript
    assert "dragstart" in javascript
    assert 'await apiRequest("/api/red-book/order"' in javascript
    assert "await saveOrder();" in javascript
    assert "saveOrderButton" not in javascript
