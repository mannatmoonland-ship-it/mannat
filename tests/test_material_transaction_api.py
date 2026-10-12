import uuid

import pytest
from werkzeug.security import generate_password_hash

from app import (
    InMemoryFirestoreStore,
    _transaction_get_collection_snapshots,
    _transaction_get_document_snapshot,
    app,
)


CSRF = "material-transaction-csrf"
SESSION_ID = "material-session"


@pytest.fixture
def material_transaction_client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.material_categories.update({
        "cement": {
            "name": "Cement",
            "unit": "bags",
            "brands": [{"id": "brand-a", "name": "Brand A"}],
        },
        "sand": {"name": "Sand", "unit": "tons"},
    })
    store.suppliers["supplier-a"] = {
        "name": "Supplier A",
        "categoryBrandAssignments": [
            {"categoryId": "cement", "brandIds": ["brand-a"]},
        ],
        "categoryIds": ["sand"],
    }
    store.material_purchases["purchase-existing"] = {
        "date": "2026-10-01",
        "categoryId": "cement",
        "categoryName": "Cement",
        "brandId": "brand-a",
        "brand": "Brand A",
        "supplierId": "supplier-a",
        "supplierName": "Supplier A",
        "quantity": 100,
        "unit": "bags",
        "rate": 500,
        "totalAmount": 50000,
        "billLink": "",
        "createdAt": "historical-created-at",
        "unrelatedField": {"keep": True},
    }
    store.material_consumptions["consumption-existing"] = {
        "date": "2026-10-02",
        "categoryId": "cement",
        "categoryName": "Cement",
        "quantity": 20,
        "unit": "bags",
        "workLocation": "Ground floor",
        "remarks": "Keep",
        "createdAt": "historical-created-at",
        "unrelatedField": {"keep": True},
    }
    user = store.save_user({
        "id": "material-user",
        "username": "material-user",
        "role": "super_admin",
        "status": "active",
        "is_active": True,
        "active_sessions": [SESSION_ID],
        "password_hash": generate_password_hash("CurrentSecure1!"),
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "material-transaction-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user["id"]
        flask_session["role"] = user["role"]
        flask_session["session_id"] = SESSION_ID
        flask_session["material_transactions_csrf"] = CSRF
    return store, client


def set_trusted_role(store, role, permissions=None):
    user = store.get_user("material-user")
    user["role"] = role
    user["custom_staff_type"] = role if role not in {"super_admin", "office_staff", "supervisor"} else ""
    store.save_user(user)
    if permissions is not None:
        store.save_custom_staff_type({"id": "custom", "name": role, "permissions": permissions})


def purchase_payload(**overrides):
    payload = {
        "date": "2026-10-03",
        "categoryId": "cement",
        "brandId": "brand-a",
        "brand": "Untrusted Brand Name",
        "supplierId": "supplier-a",
        "supplierName": "Untrusted Supplier Name",
        "quantity": 10,
        "unit": "bags",
        "rate": 500,
        "totalAmount": 5000,
        "billLink": "",
    }
    payload.update(overrides)
    return payload


def consumption_payload(**overrides):
    payload = {
        "date": "2026-10-03",
        "categoryId": "cement",
        "quantity": 10,
        "unit": "bags",
        "workLocation": "First floor",
        "remarks": "Concrete work",
    }
    payload.update(overrides)
    return payload


def create(client, kind, payload, request_id=None, *, csrf=CSRF):
    headers = {"Idempotency-Key": request_id or str(uuid.uuid4())}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return client.post(f"/api/material-transactions/{kind}", json=payload, headers=headers)


def test_transaction_document_read_unwraps_firestore_snapshot_generator():
    expected_snapshot = object()

    class Transaction:
        def get(self, _reference):
            yield expected_snapshot

    assert _transaction_get_document_snapshot(Transaction(), object()) is expected_snapshot


def test_transaction_collection_read_uses_query_snapshot_generator():
    expected_snapshots = [object(), object()]

    class Collection:
        def order_by(self, field):
            assert field == "__name__"
            return self

    class Transaction:
        def get(self, _query):
            yield from expected_snapshots

    assert _transaction_get_collection_snapshots(Transaction(), Collection()) == expected_snapshots


def mutate(client, kind, transaction_id, method, payload=None, *, csrf=CSRF):
    headers = {}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return client.open(
        f"/api/material-transactions/{kind}/{transaction_id}",
        method=method,
        json=payload,
        headers=headers,
    )


@pytest.mark.parametrize(
    ("kind", "method", "transaction_id", "payload"),
    [
        ("purchases", "POST", "", purchase_payload()),
        ("purchases", "PATCH", "purchase-existing", purchase_payload()),
        ("purchases", "DELETE", "purchase-existing", None),
        ("consumptions", "POST", "", consumption_payload()),
        ("consumptions", "PATCH", "consumption-existing", consumption_payload()),
        ("consumptions", "DELETE", "consumption-existing", None),
    ],
)
def test_all_material_transaction_mutations_require_authentication(
    material_transaction_client, kind, method, transaction_id, payload
):
    _store, client = material_transaction_client
    with client.session_transaction() as flask_session:
        flask_session.clear()
    if method == "POST":
        response = create(client, kind, payload)
    else:
        response = mutate(client, kind, transaction_id, method, payload)
    assert response.status_code == 401


@pytest.mark.parametrize("role", ["office_staff", "supervisor"])
@pytest.mark.parametrize("kind,payload", [
    ("purchases", purchase_payload()),
    ("consumptions", consumption_payload()),
])
def test_office_staff_and_supervisor_can_create_and_edit_transactions(
    material_transaction_client, role, kind, payload
):
    store, client = material_transaction_client
    set_trusted_role(store, role)
    created = create(client, kind, payload)
    assert created.status_code == 201
    transaction_id = created.get_json()["id"]
    edited = mutate(client, kind, transaction_id, "PATCH", payload)
    assert edited.status_code == 200
    assert edited.get_json() == {"id": transaction_id}


@pytest.mark.parametrize("role", ["office_staff", "supervisor"])
def test_office_staff_and_supervisor_cannot_delete_transactions_by_default(
    material_transaction_client, role
):
    store, client = material_transaction_client
    set_trusted_role(store, role)
    purchase = mutate(client, "purchases", "purchase-existing", "DELETE")
    consumption = mutate(client, "consumptions", "consumption-existing", "DELETE")
    assert purchase.status_code == 403
    assert consumption.status_code == 403
    assert "purchase-existing" in store.material_purchases
    assert "consumption-existing" in store.material_consumptions


def test_custom_role_defaults_deny_material_transaction_operations(material_transaction_client):
    store, client = material_transaction_client
    set_trusted_role(store, "Materials Clerk")
    assert create(client, "purchases", purchase_payload()).status_code == 403
    assert mutate(client, "consumptions", "consumption-existing", "DELETE").status_code == 403


def test_custom_role_permissions_are_explicit_and_operation_specific(material_transaction_client):
    store, client = material_transaction_client
    set_trusted_role(store, "Materials Clerk", ["materials.transactions.write"])
    assert create(client, "purchases", purchase_payload()).status_code == 201
    assert mutate(client, "purchases", "purchase-existing", "DELETE").status_code == 403
    set_trusted_role(store, "Materials Clerk", ["materials.transactions.delete"])
    assert mutate(client, "purchases", "purchase-existing", "DELETE").status_code == 200


def test_super_admin_can_create_edit_and_delete_purchase_and_consumption(material_transaction_client):
    _store, client = material_transaction_client
    for kind, payload in (
        ("purchases", purchase_payload()),
        ("consumptions", consumption_payload()),
    ):
        created = create(client, kind, payload)
        assert created.status_code == 201
        transaction_id = created.get_json()["id"]
        assert mutate(client, kind, transaction_id, "PATCH", payload).status_code == 200
        assert mutate(client, kind, transaction_id, "DELETE").status_code == 200


def test_unexpected_create_failure_logs_redacted_traceback(
    material_transaction_client, monkeypatch, caplog
):
    store, client = material_transaction_client
    sensitive_value = "sensitive-material-request-value"

    def fail_mutation(*_args, **_kwargs):
        raise AttributeError(sensitive_value)

    monkeypatch.setattr(store, "mutate_material_transaction", fail_mutation)
    caplog.set_level("ERROR", logger=app.logger.name)

    response = create(
        client,
        "purchases",
        purchase_payload(billLink=sensitive_value),
    )

    assert response.status_code == 503
    assert sensitive_value not in response.get_data(as_text=True)
    matching_records = [
        record
        for record in caplog.records
        if record.getMessage().startswith(
            "Material transaction creation failed (AttributeError)."
        )
    ]
    assert len(matching_records) == 1
    assert matching_records[0].exc_info is not None
    assert "AttributeError" in caplog.text
    assert "material_transaction_create_api" in caplog.text
    assert "fail_mutation" in caplog.text
    assert sensitive_value not in caplog.text


def test_non_cement_purchase_keeps_amount_based_behavior(material_transaction_client):
    store, client = material_transaction_client
    store.suppliers["supplier-sand"] = {"name": "Sand Supplier", "categoryIds": ["sand"]}
    payload = {
        "date": "2026-10-04",
        "categoryId": "sand",
        "brandId": "",
        "brand": "",
        "supplierId": "supplier-sand",
        "supplierName": "",
        "quantity": None,
        "unit": "",
        "rate": None,
        "totalAmount": 1250,
        "billLink": "",
    }
    response = create(client, "purchases", payload)
    assert response.status_code == 201
    saved = store.material_purchases[response.get_json()["id"]]
    assert saved["totalAmount"] == 1250
    assert saved["quantity"] is None
    assert saved["rate"] is None
    assert saved["supplierName"] == "Sand Supplier"


def test_cement_purchase_validates_supplier_category_and_brand(material_transaction_client):
    store, client = material_transaction_client
    store.suppliers["supplier-sand"] = {"name": "Sand Supplier", "categoryIds": ["sand"]}
    wrong_supplier = create(
        client,
        "purchases",
        purchase_payload(supplierId="supplier-sand"),
    )
    assert wrong_supplier.status_code == 400
    assert "supplier assigned" in wrong_supplier.get_json()["error"]
    wrong_brand = create(client, "purchases", purchase_payload(brandId="unknown-brand"))
    assert wrong_brand.status_code == 400
    assert "valid Cement brand" in wrong_brand.get_json()["error"]


def test_mutations_require_csrf(material_transaction_client):
    _store, client = material_transaction_client
    assert create(client, "purchases", purchase_payload(), csrf="wrong").status_code == 403
    assert mutate(
        client,
        "consumptions",
        "consumption-existing",
        "PATCH",
        consumption_payload(),
        csrf="wrong",
    ).status_code == 403
    assert mutate(
        client,
        "purchases",
        "purchase-existing",
        "DELETE",
        csrf="wrong",
    ).status_code == 403


@pytest.mark.parametrize(
    "forged",
    [
        {"role": "super_admin"},
        {"permissions": ["materials.transactions.write", "materials.transactions.delete"]},
        {"user_id": "another-user"},
    ],
)
def test_browser_supplied_authorization_fields_cannot_elevate_custom_role(
    material_transaction_client, forged
):
    store, client = material_transaction_client
    set_trusted_role(store, "Materials Clerk")
    with client.session_transaction() as flask_session:
        flask_session["role"] = "super_admin"
    response = create(client, "purchases", {**purchase_payload(), **forged})
    assert response.status_code == 403


@pytest.mark.parametrize("kind", ["purchases", "consumptions"])
def test_invalid_and_nonexistent_transaction_ids_are_safe(material_transaction_client, kind):
    _store, client = material_transaction_client
    invalid = mutate(client, kind, "bad/id", "DELETE")
    missing = mutate(client, kind, "missing-record", "DELETE")
    missing_edit = mutate(
        client,
        kind,
        "missing-record",
        "PATCH",
        purchase_payload() if kind == "purchases" else consumption_payload(),
    )
    assert invalid.status_code in {400, 404}
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "Material transaction not found."}
    assert missing_edit.status_code == 404


def test_arbitrary_transaction_collection_is_not_accepted(material_transaction_client):
    _store, client = material_transaction_client
    response = create(client, "users", purchase_payload())
    assert response.status_code == 404


def test_nonexistent_category_and_supplier_are_rejected(material_transaction_client):
    _store, client = material_transaction_client
    category = create(client, "purchases", purchase_payload(categoryId="missing"))
    supplier = create(client, "purchases", purchase_payload(supplierId="missing"))
    assert category.status_code == 404
    assert supplier.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        purchase_payload(quantity=-1, totalAmount=-500),
        purchase_payload(rate=-1, totalAmount=-10),
        purchase_payload(totalAmount=123),
        purchase_payload(date="2026-02-30"),
        purchase_payload(quantity="NaN"),
    ],
)
def test_invalid_purchase_values_are_rejected(material_transaction_client, payload):
    _store, client = material_transaction_client
    response = create(client, "purchases", payload)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        consumption_payload(quantity=0),
        consumption_payload(quantity=-1),
        consumption_payload(date="not-a-date"),
        consumption_payload(workLocation="   "),
    ],
)
def test_invalid_consumption_values_are_rejected(material_transaction_client, payload):
    _store, client = material_transaction_client
    response = create(client, "consumptions", payload)
    assert response.status_code == 400


def test_consumption_cannot_exceed_stock_and_edit_reuses_its_existing_quantity(
    material_transaction_client
):
    store, client = material_transaction_client
    too_much = create(client, "consumptions", consumption_payload(quantity=81))
    assert too_much.status_code == 409
    assert len(store.material_consumptions) == 1
    valid_edit = mutate(
        client,
        "consumptions",
        "consumption-existing",
        "PATCH",
        consumption_payload(quantity=80),
    )
    assert valid_edit.status_code == 200


def test_stock_check_includes_legacy_material_linked_transactions(material_transaction_client):
    store, client = material_transaction_client
    store.materials["cement-material"] = {"categoryId": "cement", "unit": "bags"}
    store.material_consumptions["legacy-consumption"] = {
        "materialId": "cement-material",
        "quantity": 70,
        "unit": "bags",
    }
    response = create(client, "consumptions", consumption_payload(quantity=11))
    assert response.status_code == 409
    assert len(store.material_consumptions) == 2


def test_edit_preserves_id_created_at_unrelated_fields_and_other_transactions(
    material_transaction_client
):
    store, client = material_transaction_client
    other_purchase = {"categoryId": "sand", "unrelated": "untouched"}
    store.material_purchases["purchase-other"] = other_purchase.copy()
    response = mutate(
        client,
        "purchases",
        "purchase-existing",
        "PATCH",
        purchase_payload(quantity=12, totalAmount=6000),
    )
    assert response.status_code == 200
    edited = store.material_purchases["purchase-existing"]
    assert edited["createdAt"] == "historical-created-at"
    assert edited["unrelatedField"] == {"keep": True}
    assert edited["quantity"] == 12
    assert store.material_purchases["purchase-other"] == other_purchase


def test_idempotency_key_prevents_duplicate_create_and_conflicts_on_reuse(
    material_transaction_client
):
    store, client = material_transaction_client
    request_id = str(uuid.uuid4())
    first = create(client, "purchases", purchase_payload(), request_id)
    retry = create(client, "purchases", purchase_payload(), request_id)
    collision = create(client, "purchases", purchase_payload(quantity=11, totalAmount=5500), request_id)
    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.get_json() == first.get_json()
    assert collision.status_code == 409
    assert len(store.material_purchases) == 2


def test_delete_touches_only_selected_transaction_collection(material_transaction_client):
    store, client = material_transaction_client
    response = mutate(client, "purchases", "purchase-existing", "DELETE")
    assert response.status_code == 200
    assert "purchase-existing" not in store.material_purchases
    assert "consumption-existing" in store.material_consumptions
