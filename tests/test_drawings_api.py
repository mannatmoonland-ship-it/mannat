import copy
import uuid

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from app import InMemoryFirestoreStore, app


CSRF = "drawings-csrf-test-token"
SESSION_ID = "drawings-test-session"
USER_ID = "drawings-test-user"


@pytest.fixture
def drawings_client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.drawings_categories.update({
        "category-a": {
            "name": "Plans",
            "sortOrder": 0,
            "createdAt": "category-created",
            "updatedAt": "category-updated",
            "extraCategoryField": {"keep": True},
        },
        "category-b": {
            "name": "Sections",
            "sortOrder": 1,
            "createdAt": "category-b-created",
            "updatedAt": "category-b-updated",
        },
        "category-empty": {"name": "Empty", "sortOrder": 2},
    })
    store.drawings.update({
        "drawing-a": {
            "categoryId": "category-a",
            "categoryName": "Plans",
            "drawingName": "Ground Floor",
            "drawingNumber": "A-01",
            "revision": "A",
            "googleDriveUrl": "https://drive.google.com/file/d/one",
            "description": "Plan",
            "sortOrder": 0,
            "createdAt": "drawing-created",
            "updatedAt": "drawing-updated",
            "extraDrawingField": {"keep": True},
        },
        "drawing-b": {
            "categoryId": "category-a",
            "categoryName": "Plans",
            "drawingName": "First Floor",
            "drawingNumber": "A-02",
            "revision": "B",
            "googleDriveUrl": "https://docs.google.com/document/d/two",
            "description": "Second plan",
            "sortOrder": 1,
            "createdAt": "drawing-b-created",
        },
        "drawing-c": {
            "categoryId": "category-b",
            "categoryName": "Sections",
            "drawingName": "Section A",
            "drawingNumber": "S-01",
            "revision": "",
            "googleDriveUrl": "https://drive.google.com/file/d/three",
            "description": "",
            "sortOrder": 0,
            "createdAt": "drawing-c-created",
        },
    })
    store.trades["trade-keep"] = {
        "contractItems": [{"id": "contract-keep", "amount": 1200}],
        "paymentTransactions": [{"id": "payment-keep", "amount": 300}],
    }
    store.owner_scope_records = {"owner-scope-keep": {"amount": 450}}
    store.daily_records = {"daily-record-keep": {"notes": "keep"}}
    store.material_purchases["purchase-keep"] = {"totalAmount": 250}
    user = store.save_user({
        "id": USER_ID,
        "username": "drawings-user",
        "role": "super_admin",
        "custom_staff_type": "",
        "status": "active",
        "is_active": True,
        "active_sessions": [SESSION_ID],
        "authorization_version": 1,
        "password_hash": generate_password_hash("CurrentSecure1!"),
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "drawings-api-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user["id"]
        flask_session["session_id"] = SESSION_ID
        flask_session["drawings_csrf"] = CSRF
    return store, client


def headers(csrf=CSRF):
    return {} if csrf is None else {"X-CSRF-Token": csrf}


def valid_drawing_payload(**overrides):
    payload = {
        "categoryId": "category-a",
        "drawingName": "Roof Plan",
        "drawingNumber": "A-03",
        "revision": "C",
        "googleDriveUrl": "https://drive.google.com/file/d/roof",
        "description": "Roof layout",
    }
    payload.update(overrides)
    return payload


def set_role(store, client, role, permissions=None):
    user = store.get_user(USER_ID)
    user["role"] = role
    user["custom_staff_type"] = (
        role if role not in {"super_admin", "office_staff", "supervisor"} else ""
    )
    store.save_user(user)
    if permissions is not None:
        store.save_custom_staff_type({
            "id": "drawings-custom-role",
            "name": role,
            "permissions": permissions,
        })
    with client.session_transaction() as flask_session:
        flask_session["role"] = role
        flask_session["permissions"] = list(permissions or [])


def test_unauthenticated_mutations_are_rejected(drawings_client):
    _store, client = drawings_client
    with client.session_transaction() as flask_session:
        flask_session.clear()
    response = client.post(
        "/api/drawings/categories",
        json={"id": str(uuid.uuid4()), "name": "New"},
        headers=headers(),
    )
    assert response.status_code == 401


@pytest.mark.parametrize("csrf", [None, "invalid-token"])
def test_missing_or_invalid_csrf_is_rejected(drawings_client, csrf):
    _store, client = drawings_client
    response = client.post(
        "/api/drawings/categories",
        json={"id": str(uuid.uuid4()), "name": "New"},
        headers=headers(csrf),
    )
    assert response.status_code == 403


def test_office_staff_can_write_but_cannot_delete_by_default(drawings_client):
    store, client = drawings_client
    set_role(store, client, "office_staff")
    created = client.patch(
        "/api/drawings/drawing-a",
        json=valid_drawing_payload(drawingName="Updated by staff"),
        headers=headers(),
    )
    deleted = client.delete("/api/drawings/drawing-a", headers=headers())
    assert created.status_code == 200
    assert deleted.status_code == 403
    assert "drawing-a" in store.drawings


def test_supervisor_can_write_but_cannot_delete_by_default(drawings_client):
    store, client = drawings_client
    set_role(store, client, "supervisor")
    created = client.post(
        "/api/drawings/categories",
        json={"id": str(uuid.uuid4()), "name": "Supervisor category"},
        headers=headers(),
    )
    deleted = client.delete("/api/drawings/categories/category-empty", headers=headers())
    assert created.status_code == 201
    assert deleted.status_code == 403


def test_custom_role_defaults_to_deny_and_explicit_permission_is_honored(drawings_client):
    store, client = drawings_client
    set_role(store, client, "Drafting", [])
    denied = client.post(
        "/api/drawings/categories",
        json={"id": str(uuid.uuid4()), "name": "Denied"},
        headers=headers(),
    )
    assert denied.status_code == 403

    set_role(store, client, "Drafting", ["drawings.write", "drawings.delete"])
    created = client.post(
        "/api/drawings/categories",
        json={"id": str(uuid.uuid4()), "name": "Granted"},
        headers=headers(),
    )
    deleted = client.delete("/api/drawings/drawing-a", headers=headers())
    assert created.status_code == 201
    assert deleted.status_code == 200


@pytest.mark.parametrize(
    ("field", "value"),
    [("role", "super_admin"), ("permissions", ["drawings.delete"]), ("userId", USER_ID)],
)
def test_browser_supplied_identity_or_permissions_cannot_elevate(drawings_client, field, value):
    store, client = drawings_client
    set_role(store, client, "office_staff")
    payload = {"id": str(uuid.uuid4()), "name": "Forged"}
    payload[field] = value
    response = client.post("/api/drawings/categories", json=payload, headers=headers())
    assert response.status_code == 400
    assert len(store.drawings_categories) == 3


def test_inactive_and_forced_password_change_users_are_rejected(drawings_client):
    store, client = drawings_client
    user = store.get_user(USER_ID)
    user["status"] = "inactive"
    user["is_active"] = False
    store.save_user(user)
    inactive = client.delete("/api/drawings/drawing-a", headers=headers())
    assert inactive.status_code == 401

    user["status"] = "active"
    user["is_active"] = True
    user["must_change_password"] = True
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["session_id"] = SESSION_ID
    forced = client.delete("/api/drawings/drawing-a", headers=headers())
    assert forced.status_code == 403


def test_category_and_drawing_create_are_idempotent(drawings_client):
    store, client = drawings_client
    category_id = str(uuid.uuid4())
    category_request = {"id": category_id, "name": "New Category"}
    first_category = client.post(
        "/api/drawings/categories", json=category_request, headers=headers()
    )
    retried_category = client.post(
        "/api/drawings/categories", json=category_request, headers=headers()
    )
    assert first_category.status_code == 201
    assert retried_category.status_code == 200
    assert list(store.drawings_categories).count(category_id) == 1

    drawing_id = str(uuid.uuid4())
    drawing_request = {"id": drawing_id, **valid_drawing_payload()}
    first_drawing = client.post("/api/drawings", json=drawing_request, headers=headers())
    retried_drawing = client.post("/api/drawings", json=drawing_request, headers=headers())
    assert first_drawing.status_code == 201
    assert retried_drawing.status_code == 200
    assert list(store.drawings).count(drawing_id) == 1
    assert store.drawings[drawing_id]["categoryName"] == "Plans"


def test_google_drive_sharing_url_with_query_string_is_saved_unchanged(drawings_client):
    store, client = drawings_client
    drawing_id = str(uuid.uuid4())
    drive_url = (
        "https://drive.google.com/file/d/1dZNJGE0p1vije2RId5HpcXgPx3B0ak7-/"
        "view?usp=sharing"
    )

    response = client.post(
        "/api/drawings",
        json={
            "id": drawing_id,
            **valid_drawing_payload(googleDriveUrl=drive_url),
        },
        headers=headers(),
    )

    assert response.status_code == 201
    assert response.get_json()["id"] == drawing_id
    assert store.drawings[drawing_id]["googleDriveUrl"] == drive_url


def test_unexpected_mutation_failure_logs_redacted_traceback(
    drawings_client, monkeypatch, caplog
):
    store, client = drawings_client
    sensitive_value = "sensitive-drawing-request-value"

    def fail_mutation(*_args, **_kwargs):
        raise AttributeError(sensitive_value)

    monkeypatch.setattr(store, "create_drawing", fail_mutation)
    caplog.set_level("ERROR", logger=app.logger.name)

    response = client.post(
        "/api/drawings",
        json={
            "id": str(uuid.uuid4()),
            **valid_drawing_payload(description=sensitive_value),
        },
        headers=headers(),
    )

    assert response.status_code == 503
    assert sensitive_value not in response.get_data(as_text=True)
    matching_records = [
        record
        for record in caplog.records
        if record.getMessage().startswith("Drawings mutation failed (AttributeError).")
    ]
    assert len(matching_records) == 1
    assert matching_records[0].exc_info is not None
    assert "AttributeError" in caplog.text
    assert "_drawings_mutation_result" in caplog.text
    assert "fail_mutation" in caplog.text
    assert sensitive_value not in caplog.text


def test_drawing_edit_is_targeted_and_preserves_created_and_unrelated_data(drawings_client):
    store, client = drawings_client
    before_drawing = copy.deepcopy(store.drawings["drawing-a"])
    before_other = copy.deepcopy(store.drawings["drawing-b"])
    other_data = (
        copy.deepcopy(store.trades),
        copy.deepcopy(store.owner_scope_records),
        copy.deepcopy(store.daily_records),
        copy.deepcopy(store.material_purchases),
    )
    response = client.patch(
        "/api/drawings/drawing-a",
        json=valid_drawing_payload(
            drawingName="Updated Roof",
            categoryId="category-b",
            googleDriveUrl="https://drive.google.com/file/d/updated",
        ),
        headers=headers(),
    )
    assert response.status_code == 200
    edited = store.drawings["drawing-a"]
    assert edited["drawingName"] == "Updated Roof"
    assert edited["categoryName"] == "Sections"
    assert edited["createdAt"] == before_drawing["createdAt"]
    assert edited["extraDrawingField"] == before_drawing["extraDrawingField"]
    assert edited["sortOrder"] == 1
    assert store.drawings["drawing-b"] == before_other
    assert (
        store.trades,
        store.owner_scope_records,
        store.daily_records,
        store.material_purchases,
    ) == other_data


def test_category_rename_propagates_name_without_replacing_documents(drawings_client):
    store, client = drawings_client
    before_category = copy.deepcopy(store.drawings_categories["category-a"])
    before_drawing = copy.deepcopy(store.drawings["drawing-a"])
    before_other = copy.deepcopy(store.drawings["drawing-c"])
    response = client.patch(
        "/api/drawings/categories/category-a",
        json={"name": "Architectural"},
        headers=headers(),
    )
    assert response.status_code == 200
    category = store.drawings_categories["category-a"]
    drawing = store.drawings["drawing-a"]
    assert category["name"] == "Architectural"
    assert category["sortOrder"] == before_category["sortOrder"]
    assert category["createdAt"] == before_category["createdAt"]
    assert category["extraCategoryField"] == before_category["extraCategoryField"]
    assert drawing["categoryId"] == before_drawing["categoryId"]
    assert drawing["categoryName"] == "Architectural"
    assert drawing["createdAt"] == before_drawing["createdAt"]
    assert drawing["extraDrawingField"] == before_drawing["extraDrawingField"]
    assert store.drawings["drawing-c"] == before_other


def test_category_delete_is_blocked_with_drawings_and_allowed_when_empty(drawings_client):
    store, client = drawings_client
    linked = client.delete("/api/drawings/categories/category-a", headers=headers())
    assert linked.status_code == 409
    assert "category-a" in store.drawings_categories
    assert "drawing-a" in store.drawings

    empty = client.delete("/api/drawings/categories/category-empty", headers=headers())
    assert empty.status_code == 200
    assert "category-empty" not in store.drawings_categories
    assert "drawing-a" in store.drawings


def test_reorders_update_only_order_fields_and_reject_arbitrary_ids(drawings_client):
    store, client = drawings_client
    categories_before = copy.deepcopy(store.drawings_categories)
    drawings_before = copy.deepcopy(store.drawings)
    category_response = client.post(
        "/api/drawings/categories/order",
        json={"ids": ["category-b", "category-a", "category-empty"]},
        headers=headers(),
    )
    drawing_response = client.post(
        "/api/drawings/categories/category-a/order",
        json={"ids": ["drawing-b", "drawing-a"]},
        headers=headers(),
    )
    invalid = client.post(
        "/api/drawings/categories/category-a/order",
        json={"ids": ["drawing-a", "trade-keep"]},
        headers=headers(),
    )
    assert category_response.status_code == 200
    assert drawing_response.status_code == 200
    assert invalid.status_code == 409
    assert store.drawings_categories["category-b"]["sortOrder"] == 0
    assert store.drawings_categories["category-a"]["sortOrder"] == 1
    assert store.drawings["drawing-b"]["sortOrder"] == 0
    assert store.drawings["drawing-a"]["sortOrder"] == 1
    for item_id, before in categories_before.items():
        for field, value in before.items():
            if field != "sortOrder":
                assert store.drawings_categories[item_id][field] == value
    for item_id, before in drawings_before.items():
        for field, value in before.items():
            if field != "sortOrder" and item_id in {"drawing-a", "drawing-b"}:
                assert store.drawings[item_id][field] == value
    assert store.drawings["drawing-c"] == drawings_before["drawing-c"]


def test_delete_removes_only_requested_record_and_preserves_related_collections(drawings_client):
    store, client = drawings_client
    trades_before = copy.deepcopy(store.trades)
    other_collections_before = (
        copy.deepcopy(store.owner_scope_records),
        copy.deepcopy(store.daily_records),
        copy.deepcopy(store.material_purchases),
    )
    response = client.delete("/api/drawings/drawing-a", headers=headers())
    assert response.status_code == 200
    assert "drawing-a" not in store.drawings
    assert "drawing-b" in store.drawings
    assert "drawing-c" in store.drawings
    assert store.drawings_categories["category-a"]["name"] == "Plans"
    assert store.trades == trades_before
    assert (
        store.owner_scope_records,
        store.daily_records,
        store.material_purchases,
    ) == other_collections_before


@pytest.mark.parametrize(
    ("path", "method", "payload"),
    [
        ("/api/drawings/not-a-real-id", "DELETE", None),
        ("/api/drawings/categories/not-a-real-id", "DELETE", None),
        ("/api/drawings/drawing-a", "PATCH", {"name": "Whole document replacement"}),
        ("/api/drawings", "POST", {"id": "not-a-uuid", **valid_drawing_payload()}),
    ],
)
def test_invalid_identifiers_and_unsupported_payloads_fail_safely(
    drawings_client, path, method, payload
):
    _store, client = drawings_client
    response = client.open(
        path,
        method=method,
        json=payload,
        headers=headers(),
    )
    assert response.status_code in {400, 404}


def test_missing_record_returns_safe_not_found(drawings_client):
    _store, client = drawings_client
    response = client.delete("/api/drawings/missing-drawing", headers=headers())
    assert response.status_code == 404
    assert response.is_json


def test_external_drive_resource_is_not_fetched_or_deleted(drawings_client, monkeypatch):
    store, client = drawings_client
    fetched = []
    monkeypatch.setattr(
        app_module.requests, "get", lambda *args, **kwargs: fetched.append(args)
    )
    response = client.delete("/api/drawings/drawing-a", headers=headers())
    assert response.status_code == 200
    assert fetched == []
