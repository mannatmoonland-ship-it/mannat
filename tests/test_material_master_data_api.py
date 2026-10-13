import copy
import uuid

import pytest
from werkzeug.security import generate_password_hash

from app import InMemoryFirestoreStore, app


CSRF = "material-master-csrf-token"
SESSION_ID = "material-master-session"
USER_ID = "material-master-user"


@pytest.fixture
def material_master_client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.material_categories.update({
        "cement": {
            "name": "Cement",
            "unit": "bags",
            "brands": [{"id": "brand-a", "name": "Brand A"}],
            "description": "Cement category",
            "createdAt": "category-created",
            "updatedAt": "category-updated",
            "unrelatedField": {"preserve": True},
        },
        "sand": {
            "name": "Sand",
            "unit": "tons",
            "brands": [{"id": "brand-s", "name": "River Sand"}],
            "description": "",
            "createdAt": "sand-created",
        },
        "empty": {
            "name": "Unused",
            "unit": "pieces",
            "brands": [],
            "description": "",
        },
        "chips": {
            "name": "20 mm Chips",
            "unit": "tons",
            "brands": [],
            "description": "",
        },
        "metal": {
            "name": "40 mm Metal",
            "unit": "tons",
            "brands": [{"id": "quarry-metal", "name": "Emulator Quarry"}],
            "description": "",
        },
    })
    store.materials["cement-material"] = {
        "name": "Legacy Cement",
        "categoryId": "cement",
        "unit": "bags",
    }
    store.material_purchases.update({
        "purchase-existing": {
            "categoryId": "cement",
            "materialId": "cement-material",
            "supplierId": "supplier-a",
            "brandId": "brand-a",
            "brand": "Brand A",
            "totalAmount": 1250,
            "createdAt": "purchase-created",
        },
    })
    store.material_consumptions["consumption-existing"] = {
        "categoryId": "cement",
        "materialId": "cement-material",
        "quantity": 2,
        "createdAt": "consumption-created",
    }
    store.suppliers.update({
        "supplier-a": {
            "name": "Supplier A",
            "categoryIds": ["cement"],
            "categoryBrandAssignments": [{
                "categoryId": "cement",
                "brandIds": ["brand-a"],
            }],
            "contactPerson": "Contact",
            "address": "Address",
            "city": "City",
            "state": "State",
            "pinCode": "123456",
            "primaryPhone": "1234567890",
            "secondaryPhone": "",
            "whatsappNumber": "",
            "email": "",
            "gstNumber": "",
            "notes": "",
            "createdAt": "supplier-created",
            "unrelatedField": {"preserve": True},
        },
        "supplier-unused": {
            "name": "Unused Supplier",
            "categoryIds": ["sand"],
            "categoryBrandAssignments": [{
                "categoryId": "sand",
                "brandIds": ["brand-s"],
            }],
            "primaryPhone": "9876543210",
        },
    })
    user = store.save_user({
        "id": USER_ID,
        "username": "material-master-user",
        "role": "super_admin",
        "custom_staff_type": "",
        "status": "active",
        "is_active": True,
        "active_sessions": [SESSION_ID],
        "authorization_version": 1,
        "password_hash": generate_password_hash("CurrentSecure1!"),
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "material-master-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user["id"]
        flask_session["session_id"] = SESSION_ID
        flask_session["material_transactions_csrf"] = CSRF
    return store, client


def csrf_headers(token=CSRF):
    return {} if token is None else {"X-CSRF-Token": token}


def category_payload(**overrides):
    payload = {
        "name": "Aggregate",
        "unit": "tons",
        "brands": [{"id": "aggregate-a", "name": "Aggregate A"}],
        "description": "Aggregate materials",
    }
    payload.update(overrides)
    return payload


def supplier_payload(**overrides):
    payload = {
        "name": "New Supplier",
        "categoryIds": ["sand"],
        "categoryBrandAssignments": [{
            "categoryId": "sand",
            "brandIds": ["brand-s"],
        }],
        "contactPerson": "",
        "address": "",
        "city": "",
        "state": "",
        "pinCode": "",
        "primaryPhone": "5551234567",
        "secondaryPhone": "",
        "whatsappNumber": "",
        "email": "",
        "gstNumber": "",
        "notes": "",
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
            "id": "material-master-role",
            "name": role,
            "permissions": permissions,
        })
    with client.session_transaction() as flask_session:
        flask_session["role"] = role
        flask_session["permissions"] = list(permissions or [])


def test_unauthenticated_master_data_write_is_rejected(material_master_client):
    _store, client = material_master_client
    with client.session_transaction() as flask_session:
        flask_session.clear()
    response = client.post(
        "/api/material-master/categories",
        json={"id": str(uuid.uuid4()), **category_payload()},
        headers=csrf_headers(),
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("path", "method", "payload"),
    [
        (
            "/api/material-master/categories",
            "POST",
            {"id": str(uuid.uuid4()), **category_payload()},
        ),
        (
            "/api/material-master/categories/sand",
            "PATCH",
            category_payload(name="Updated", unit="tons", brands=[{
                "id": "brand-s",
                "name": "River Sand",
            }]),
        ),
        ("/api/material-master/categories/empty", "DELETE", None),
        (
            "/api/material-master/suppliers",
            "POST",
            {"id": str(uuid.uuid4()), **supplier_payload()},
        ),
        (
            "/api/material-master/suppliers/supplier-unused",
            "PATCH",
            supplier_payload(),
        ),
        ("/api/material-master/suppliers/supplier-unused", "DELETE", None),
    ],
)
def test_each_master_data_mutation_requires_authentication(
    material_master_client, path, method, payload
):
    _store, client = material_master_client
    with client.session_transaction() as flask_session:
        flask_session.clear()
    response = client.open(
        path,
        method=method,
        json=payload,
        headers=csrf_headers(),
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("path", "method", "payload"),
    [
        (
            "/api/material-master/categories",
            "POST",
            {"id": str(uuid.uuid4()), **category_payload()},
        ),
        (
            "/api/material-master/categories/sand",
            "PATCH",
            category_payload(name="Updated", unit="tons", brands=[{
                "id": "brand-s",
                "name": "River Sand",
            }]),
        ),
        (
            "/api/material-master/suppliers",
            "POST",
            {"id": str(uuid.uuid4()), **supplier_payload()},
        ),
        (
            "/api/material-master/suppliers/supplier-unused",
            "PATCH",
            supplier_payload(),
        ),
    ],
)
def test_each_master_data_write_requires_materials_write_permission(
    material_master_client, path, method, payload
):
    store, client = material_master_client
    set_role(store, client, "Drafting", [])
    response = client.open(
        path,
        method=method,
        json=payload,
        headers=csrf_headers(),
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("path", "category_id"),
    [
        ("/api/material-master/categories/empty", "empty"),
        ("/api/material-master/suppliers/supplier-unused", "supplier-unused"),
    ],
)
def test_custom_role_can_delete_only_with_compiled_materials_delete(
    material_master_client, path, category_id
):
    store, client = material_master_client
    set_role(store, client, "Materials Admin", ["materials.delete"])
    response = client.delete(path, headers=csrf_headers())
    assert response.status_code == 200
    if category_id == "empty":
        assert category_id not in store.material_categories
    else:
        assert category_id not in store.suppliers


def test_forged_session_role_and_permissions_do_not_elevate_authorization(
    material_master_client,
):
    store, client = material_master_client
    set_role(store, client, "office_staff")
    with client.session_transaction() as flask_session:
        flask_session["role"] = "super_admin"
        flask_session["permissions"] = ["materials.delete"]
    response = client.delete(
        "/api/material-master/suppliers/supplier-unused", headers=csrf_headers()
    )
    assert response.status_code == 403


@pytest.mark.parametrize("token", [None, "invalid"])
def test_missing_and_invalid_csrf_reject_master_data_changes(material_master_client, token):
    _store, client = material_master_client
    response = client.patch(
        "/api/material-master/categories/cement",
        json=category_payload(name="New Name"),
        headers=csrf_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["office_staff", "supervisor"])
def test_operational_roles_can_write_but_cannot_delete_master_data(
    material_master_client, role
):
    store, client = material_master_client
    set_role(store, client, role)
    updated = client.patch(
        "/api/material-master/categories/sand",
        json=category_payload(name="Sand Updated", unit="tons", brands=[{
            "id": "brand-s",
            "name": "River Sand",
        }]),
        headers=csrf_headers(),
    )
    deleted = client.delete(
        "/api/material-master/suppliers/supplier-unused", headers=csrf_headers()
    )
    assert updated.status_code == 200
    assert deleted.status_code == 403
    assert "supplier-unused" in store.suppliers


def test_custom_role_defaults_to_deny_and_explicit_write_grant_works(
    material_master_client,
):
    store, client = material_master_client
    set_role(store, client, "Purchasing", [])
    denied = client.patch(
        "/api/material-master/categories/sand",
        json=category_payload(name="Denied", unit="tons", brands=[{
            "id": "brand-s",
            "name": "River Sand",
        }]),
        headers=csrf_headers(),
    )
    assert denied.status_code == 403
    set_role(store, client, "Purchasing", ["materials.write"])
    allowed = client.patch(
        "/api/material-master/categories/sand",
        json=category_payload(name="Allowed", unit="tons", brands=[{
            "id": "brand-s",
            "name": "River Sand",
        }]),
        headers=csrf_headers(),
    )
    assert allowed.status_code == 200


@pytest.mark.parametrize(
    ("route", "payload"),
    [
        ("/api/material-master/categories", {"id": str(uuid.uuid4()), **category_payload()}),
        ("/api/material-master/suppliers", {"id": str(uuid.uuid4()), **supplier_payload()}),
    ],
)
@pytest.mark.parametrize(
    ("forged_field", "forged_value"),
    [
        ("role", "super_admin"),
        ("permissions", ["materials.delete"]),
        ("userId", USER_ID),
    ],
)
def test_browser_supplied_identity_or_permissions_are_rejected(
    material_master_client, route, payload, forged_field, forged_value
):
    store, client = material_master_client
    set_role(store, client, "office_staff")
    payload = dict(payload)
    payload[forged_field] = forged_value
    before = copy.deepcopy((store.material_categories, store.suppliers))
    response = client.post(route, json=payload, headers=csrf_headers())
    assert response.status_code == 400
    assert (store.material_categories, store.suppliers) == before


def test_inactive_and_forced_password_change_users_are_rejected(material_master_client):
    store, client = material_master_client
    user = store.get_user(USER_ID)
    user.update({"status": "inactive", "is_active": False})
    store.save_user(user)
    inactive = client.delete(
        "/api/material-master/categories/empty", headers=csrf_headers()
    )
    assert inactive.status_code == 401

    user.update({"status": "active", "is_active": True, "must_change_password": True})
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["session_id"] = SESSION_ID
    forced = client.delete(
        "/api/material-master/categories/empty", headers=csrf_headers()
    )
    assert forced.status_code == 403


def test_category_create_replay_is_idempotent_and_conflicting_reuse_fails(
    material_master_client,
):
    store, client = material_master_client
    category_id = str(uuid.uuid4())
    payload = {"id": category_id, **category_payload()}
    first = client.post(
        "/api/material-master/categories", json=payload, headers=csrf_headers()
    )
    replay = client.post(
        "/api/material-master/categories", json=payload, headers=csrf_headers()
    )
    conflict = client.post(
        "/api/material-master/categories",
        json={"id": category_id, **category_payload(name="Changed")},
        headers=csrf_headers(),
    )
    assert first.status_code == 201
    assert replay.status_code == 200
    assert conflict.status_code == 409
    assert list(store.material_categories).count(category_id) == 1


def test_supplier_create_replay_is_idempotent_and_conflicting_reuse_fails(
    material_master_client,
):
    store, client = material_master_client
    supplier_id = str(uuid.uuid4())
    payload = {"id": supplier_id, **supplier_payload()}
    first = client.post(
        "/api/material-master/suppliers", json=payload, headers=csrf_headers()
    )
    replay = client.post(
        "/api/material-master/suppliers", json=payload, headers=csrf_headers()
    )
    conflict = client.post(
        "/api/material-master/suppliers",
        json={"id": supplier_id, **supplier_payload(name="Changed")},
        headers=csrf_headers(),
    )
    assert first.status_code == 201
    assert replay.status_code == 200
    assert conflict.status_code == 409
    assert list(store.suppliers).count(supplier_id) == 1


def test_supplier_can_be_created_and_edited_for_category_without_brand_options(
    material_master_client,
):
    store, client = material_master_client
    supplier_id = str(uuid.uuid4())
    payload = supplier_payload(
        categoryIds=["chips"],
        categoryBrandAssignments=[{"categoryId": "chips", "brandIds": []}],
    )

    created = client.post(
        "/api/material-master/suppliers",
        json={"id": supplier_id, **payload},
        headers=csrf_headers(),
    )
    assert created.status_code == 201
    assert store.suppliers[supplier_id]["categoryBrandAssignments"] == [
        {"categoryId": "chips", "brandIds": []}
    ]

    updated = client.patch(
        f"/api/material-master/suppliers/{supplier_id}",
        json={**payload, "name": "Independent Supplier", "primaryPhone": "5559876543"},
        headers=csrf_headers(),
    )
    assert updated.status_code == 200
    assert store.suppliers[supplier_id]["name"] == "Independent Supplier"
    assert store.suppliers[supplier_id]["primaryPhone"] == "5559876543"
    assert store.suppliers[supplier_id]["categoryBrandAssignments"] == [
        {"categoryId": "chips", "brandIds": []}
    ]


def test_supplier_can_be_assigned_a_configured_quarry_for_aggregate_category(
    material_master_client,
):
    store, client = material_master_client
    payload = supplier_payload(
        categoryIds=["metal"],
        categoryBrandAssignments=[{
            "categoryId": "metal",
            "brandIds": ["quarry-metal"],
        }],
    )

    response = client.post(
        "/api/material-master/suppliers",
        json={"id": str(uuid.uuid4()), **payload},
        headers=csrf_headers(),
    )

    assert response.status_code == 201
    supplier = store.suppliers[response.get_json()["id"]]
    assert supplier["categoryBrandAssignments"] == [{
        "categoryId": "metal",
        "brandIds": ["quarry-metal"],
    }]


def test_supplier_assignment_requires_valid_brand_when_category_has_options(
    material_master_client,
):
    store, client = material_master_client
    payload = supplier_payload(
        categoryIds=["cement"],
        categoryBrandAssignments=[{"categoryId": "cement", "brandIds": []}],
    )

    response = client.post(
        "/api/material-master/suppliers",
        json={"id": str(uuid.uuid4()), **payload},
        headers=csrf_headers(),
    )

    assert response.status_code == 400
    assert "Select at least one valid brand or quarry" in response.get_json()["error"]
    assert all(supplier["name"] != payload["name"] for supplier in store.suppliers.values())


def test_supplier_assignment_rejects_brand_id_not_configured_for_category(
    material_master_client,
):
    store, client = material_master_client
    payload = supplier_payload(
        categoryIds=["chips"],
        categoryBrandAssignments=[{"categoryId": "chips", "brandIds": ["not-configured"]}],
    )

    response = client.post(
        "/api/material-master/suppliers",
        json={"id": str(uuid.uuid4()), **payload},
        headers=csrf_headers(),
    )

    assert response.status_code == 400
    assert "Select at least one valid brand or quarry" in response.get_json()["error"]
    assert all(supplier["name"] != payload["name"] for supplier in store.suppliers.values())


def test_unexpected_create_failure_logs_redacted_traceback(
    material_master_client, monkeypatch, caplog
):
    store, client = material_master_client
    sensitive_value = "sensitive-master-request-value"

    def fail_mutation(*_args, **_kwargs):
        raise AttributeError(sensitive_value)

    monkeypatch.setattr(store, "mutate_material_category", fail_mutation)
    caplog.set_level("ERROR", logger=app.logger.name)

    category_id = str(uuid.uuid4())
    response = client.post(
        "/api/material-master/categories",
        json={"id": category_id, **category_payload(description=sensitive_value)},
        headers=csrf_headers(),
    )

    assert response.status_code == 503
    assert sensitive_value not in response.get_data(as_text=True)
    matching_records = [
        record
        for record in caplog.records
        if record.getMessage().startswith(
            "Material master-data create failed (AttributeError)."
        )
    ]
    assert len(matching_records) == 1
    assert matching_records[0].exc_info is not None
    assert "AttributeError" in caplog.text
    assert "material_category_create_api" in caplog.text
    assert "fail_mutation" in caplog.text
    assert sensitive_value not in caplog.text


def test_category_edit_preserves_id_created_at_unrelated_fields_and_transactions(
    material_master_client,
):
    store, client = material_master_client
    before_category = copy.deepcopy(store.material_categories["cement"])
    before_purchase = copy.deepcopy(store.material_purchases)
    before_consumption = copy.deepcopy(store.material_consumptions)
    before_materials = copy.deepcopy(store.materials)
    response = client.patch(
        "/api/material-master/categories/cement",
        json=category_payload(
            name="Cement Updated",
            unit="bags",
            brands=[{"id": "brand-a", "name": "Brand A Updated"}],
        ),
        headers=csrf_headers(),
    )
    assert response.status_code == 200
    category = store.material_categories["cement"]
    assert category["name"] == "Cement Updated"
    assert category["createdAt"] == before_category["createdAt"]
    assert category["unrelatedField"] == before_category["unrelatedField"]
    assert category["brands"] == [{"id": "brand-a", "name": "Brand A Updated"}]
    assert store.material_categories["sand"]["name"] == "Sand"
    assert store.material_purchases == before_purchase
    assert store.material_consumptions == before_consumption
    assert store.materials == before_materials


def test_category_unit_is_locked_by_transaction_history(material_master_client):
    store, client = material_master_client
    response = client.patch(
        "/api/material-master/categories/cement",
        json=category_payload(name="Cement", unit="tons", brands=[{
            "id": "brand-a",
            "name": "Brand A",
        }]),
        headers=csrf_headers(),
    )
    assert response.status_code == 409
    assert store.material_categories["cement"]["unit"] == "bags"


def test_category_delete_refuses_material_supplier_and_transaction_references(
    material_master_client,
):
    store, client = material_master_client
    blocked_by_refs = client.delete(
        "/api/material-master/categories/cement", headers=csrf_headers()
    )
    assert blocked_by_refs.status_code == 409
    assert "cement" in store.material_categories
    assert store.material_purchases["purchase-existing"]["totalAmount"] == 1250
    assert "consumption-existing" in store.material_consumptions
    assert "cement-material" in store.materials

    deleted = client.delete(
        "/api/material-master/categories/empty", headers=csrf_headers()
    )
    assert deleted.status_code == 200
    assert "empty" not in store.material_categories
    assert "cement" in store.material_categories


def test_supplier_edit_preserves_id_created_at_and_unrelated_fields(material_master_client):
    store, client = material_master_client
    before = copy.deepcopy(store.suppliers["supplier-a"])
    payload = supplier_payload(
        name="Supplier A Updated",
        categoryIds=["cement"],
        categoryBrandAssignments=[{
            "categoryId": "cement",
            "brandIds": ["brand-a"],
        }],
        primaryPhone="1112223333",
    )
    response = client.patch(
        "/api/material-master/suppliers/supplier-a",
        json=payload,
        headers=csrf_headers(),
    )
    assert response.status_code == 200
    supplier = store.suppliers["supplier-a"]
    assert supplier["name"] == "Supplier A Updated"
    assert supplier["primaryPhone"] == "1112223333"
    assert supplier["createdAt"] == before["createdAt"]
    assert supplier["unrelatedField"] == before["unrelatedField"]
    assert "supplier-unused" in store.suppliers
    assert store.material_purchases["purchase-existing"]["supplierId"] == "supplier-a"


def test_supplier_delete_refuses_historical_purchase_and_preserves_transactions(
    material_master_client,
):
    store, client = material_master_client
    before_purchase = copy.deepcopy(store.material_purchases)
    blocked = client.delete(
        "/api/material-master/suppliers/supplier-a", headers=csrf_headers()
    )
    assert blocked.status_code == 409
    assert "supplier-a" in store.suppliers
    assert store.material_purchases == before_purchase

    deleted = client.delete(
        "/api/material-master/suppliers/supplier-unused", headers=csrf_headers()
    )
    assert deleted.status_code == 200
    assert "supplier-unused" not in store.suppliers
    assert store.material_purchases == before_purchase
    assert "consumption-existing" in store.material_consumptions


@pytest.mark.parametrize(
    ("path", "method", "payload"),
    [
        ("/api/material-master/categories/bad/path", "DELETE", None),
        ("/api/material-master/suppliers/bad/path", "DELETE", None),
        ("/api/material-master/categories/cement", "PATCH", {"name": "Whole doc"}),
        ("/api/material-master/suppliers/supplier-a", "PATCH", {"role": "super_admin"}),
    ],
)
def test_invalid_ids_and_unsupported_fields_fail_safely(
    material_master_client, path, method, payload
):
    _store, client = material_master_client
    response = client.open(
        path,
        method=method,
        json=payload,
        headers=csrf_headers(),
    )
    assert response.status_code in {400, 404}
