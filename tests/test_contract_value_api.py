import copy

import pytest
from werkzeug.security import generate_password_hash

from app import InMemoryFirestoreStore, app


@pytest.fixture
def contract_client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.trades["trade-1"] = {
        "name": "Masonry",
        "contractItems": [
            {
                "id": "contract-1",
                "name": "Wall",
                "amount": 10000,
                "createdAt": "2026-10-01T10:00:00Z",
                "updatedAt": "2026-10-01T10:00:00Z",
                "legacyField": "preserve",
            }
        ],
        "paymentTransactions": [
            {
                "id": "payment-1",
                "amount": 300,
                "type": "labour_advance",
                "remarks": "historical",
            }
        ],
        "workerCategories": [{"id": "worker-1", "name": "Mason"}],
        "unrelatedField": {"keep": True},
        "updatedAt": "2026-10-01T10:00:00Z",
    }
    store.save_user({
        "id": "user-1",
        "username": "admin",
        "role": "super_admin",
        "custom_staff_type": "",
        "status": "active",
        "is_active": True,
        "active_sessions": ["session-1"],
        "authorization_version": 3,
        "password_hash": generate_password_hash("CurrentSecure1!"),
        "permissions": [],
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "contract-api-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "user-1"
        flask_session["role"] = "super_admin"
        flask_session["session_id"] = "session-1"
        flask_session["contract_values_csrf"] = "contract-csrf-token"
    return store, client


def contract_headers(csrf=True):
    headers = {}
    if csrf:
        headers["X-CSRF-Token"] = "contract-csrf-token"
    return headers


def test_labour_payments_page_provides_contract_csrf_only_to_authorized_users(contract_client):
    store, client = contract_client

    authorized = client.get("/labour-payments")

    assert authorized.status_code == 200
    authorized_html = authorized.get_data(as_text=True)
    assert 'data-can-edit-contract-values="true"' in authorized_html
    assert 'data-contract-values-csrf=""' not in authorized_html
    with client.session_transaction() as flask_session:
        authorized_token = flask_session["contract_values_csrf"]
    assert f'data-contract-values-csrf="{authorized_token}"' in authorized_html
    created = client.post(
        "/api/trades/trade-1/contract-items",
        json={"name": "Roof", "amount": 2500},
        headers={"X-CSRF-Token": authorized_token},
    )
    assert created.status_code == 201

    user = store.get_user("user-1")
    user["role"] = "office_staff"
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["role"] = "office_staff"

    unauthorized = client.get("/labour-payments")

    assert unauthorized.status_code == 200
    unauthorized_html = unauthorized.get_data(as_text=True)
    assert 'data-can-edit-contract-values="false"' in unauthorized_html
    assert 'data-contract-values-csrf=""' in unauthorized_html
    denied = client.post(
        "/api/trades/trade-1/contract-items",
        json={"name": "Floor", "amount": 1000},
        headers={"X-CSRF-Token": authorized_token},
    )
    assert denied.status_code == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_unauthenticated_contract_mutations_are_rejected(contract_client, method):
    _store, client = contract_client
    with client.session_transaction() as flask_session:
        flask_session.clear()
    path = "/api/trades/trade-1/contract-items"
    if method in {"PATCH", "DELETE"}:
        path += "/contract-1"
    payload = {"name": "Wall", "amount": 10000} if method in {"POST", "PATCH"} else {"contractItems": []} if method == "PUT" else None

    response = client.open(path, method=method, json=payload, headers=contract_headers())

    assert response.status_code == 401


@pytest.mark.parametrize("role", ["office_staff", "supervisor"])
def test_operational_roles_cannot_edit_contract_values(contract_client, role):
    store, client = contract_client
    user = store.get_user("user-1")
    user["role"] = role
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["role"] = role

    response = client.put(
        "/api/trades/trade-1/contract-items",
        json={"contractItems": [{"id": "contract-1", "name": "Wall", "amount": 20000}]},
        headers=contract_headers(),
    )

    assert response.status_code == 403


def test_custom_role_without_contract_permission_is_denied(contract_client):
    store, client = contract_client
    user = store.get_user("user-1")
    user.update({"role": "Site Reviewer", "custom_staff_type": "Site Reviewer"})
    store.save_user(user)
    store.save_custom_staff_type({"id": "role-1", "name": "Site Reviewer"})
    with client.session_transaction() as flask_session:
        flask_session["role"] = "Site Reviewer"

    response = client.post(
        "/api/trades/trade-1/contract-items",
        json={"name": "Wall", "amount": 12000},
        headers=contract_headers(),
    )

    assert response.status_code == 403


def test_custom_role_with_explicit_contract_permission_is_allowed(contract_client):
    store, client = contract_client
    user = store.get_user("user-1")
    user.update({"role": "Contract Editor", "custom_staff_type": "Contract Editor"})
    store.save_user(user)
    store.save_custom_staff_type({
        "id": "role-1",
        "name": "Contract Editor",
        "permissions": ["contract_values.edit"],
    })
    with client.session_transaction() as flask_session:
        flask_session["role"] = "Contract Editor"

    response = client.post(
        "/api/trades/trade-1/contract-items",
        json={"name": "Roof", "amount": 2500},
        headers=contract_headers(),
    )

    assert response.status_code == 201


def test_super_admin_can_create_edit_delete_and_replace_contract_items(contract_client):
    store, client = contract_client
    created = client.post(
        "/api/trades/trade-1/contract-items",
        json={"name": "Roof", "amount": 5000},
        headers=contract_headers(),
    )
    assert created.status_code == 201
    new_id = created.get_json()["contractItems"][1]["id"]

    edited = client.patch(
        f"/api/trades/trade-1/contract-items/{new_id}",
        json={"name": "Roofing", "amount": 6000},
        headers=contract_headers(),
    )
    assert edited.status_code == 200
    assert edited.get_json()["contractItems"][1]["name"] == "Roofing"

    deleted = client.delete(
        f"/api/trades/trade-1/contract-items/{new_id}",
        headers=contract_headers(),
    )
    assert deleted.status_code == 200
    assert [item["id"] for item in store.trades["trade-1"]["contractItems"]] == ["contract-1"]

    replaced = client.put(
        "/api/trades/trade-1/contract-items",
        json={"contractItems": [{"id": "contract-1", "name": "Wall", "amount": 11000}]},
        headers=contract_headers(),
    )
    assert replaced.status_code == 200
    assert replaced.get_json()["contractItems"][0]["id"] == "contract-1"


def test_contract_mutation_requires_csrf(contract_client):
    _store, client = contract_client

    response = client.post(
        "/api/trades/trade-1/contract-items",
        json={"name": "Roof", "amount": 5000},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("role", "permissions"),
    [
        ("office_staff", ["contract_values.edit"]),
        ("Site Reviewer", ["contract_values.edit"]),
    ],
)
def test_browser_cannot_elevate_role_or_permissions(contract_client, role, permissions):
    store, client = contract_client
    user = store.get_user("user-1")
    user["role"] = "office_staff"
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["role"] = "office_staff"

    response = client.post(
        "/api/trades/trade-1/contract-items",
        json={
            "name": "Roof",
            "amount": 5000,
            "role": role,
            "permissions": permissions,
            "user_id": "another-user",
        },
        headers=contract_headers(),
    )

    assert response.status_code == 403


def test_invalid_trade_returns_safe_error(contract_client):
    _store, client = contract_client

    response = client.post(
        "/api/trades/missing-trade/contract-items",
        json={"name": "Roof", "amount": 1000},
        headers=contract_headers(),
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "Trade not found."}


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"name": "", "amount": 100}, 400),
        ({"name": "Wall", "amount": -1}, 400),
        ({"name": "Wall", "amount": "not-money"}, 400),
        ({"name": "Wall", "amount": "100"}, 400),
        ({"name": "Wall", "amount": float("inf")}, 400),
        ({"name": "Wall", "amount": True}, 400),
    ],
)
def test_invalid_contract_item_data_is_rejected_without_changes(contract_client, payload, status):
    store, client = contract_client
    before = copy.deepcopy(store.trades["trade-1"])

    response = client.post(
        "/api/trades/trade-1/contract-items",
        json=payload,
        headers=contract_headers(),
    )

    assert response.status_code == status
    assert store.trades["trade-1"] == before


def test_update_preserves_ids_payment_transactions_and_unrelated_trade_fields(contract_client):
    store, client = contract_client
    before = copy.deepcopy(store.trades["trade-1"])

    response = client.put(
        "/api/trades/trade-1/contract-items",
        json={
            "contractItems": [
                {"id": "contract-1", "name": "Wall revised", "amount": 12500},
                {"name": "Roof", "amount": 7500},
            ]
        },
        headers=contract_headers(),
    )

    assert response.status_code == 200
    updated = store.trades["trade-1"]
    assert updated["contractItems"][0]["id"] == "contract-1"
    assert updated["contractItems"][0]["legacyField"] == "preserve"
    assert updated["contractItems"][0]["createdAt"] == before["contractItems"][0]["createdAt"]
    assert updated["paymentTransactions"] == before["paymentTransactions"]
    for field in ("name", "workerCategories", "unrelatedField"):
        assert updated[field] == before[field]
    assert set(updated) == set(before)


def test_browser_cannot_supply_arbitrary_contract_item_ids(contract_client):
    store, client = contract_client

    response = client.put(
        "/api/trades/trade-1/contract-items",
        json={"contractItems": [{"id": "forged-id", "name": "Roof", "amount": 7000}]},
        headers=contract_headers(),
    )

    assert response.status_code == 200
    created_item = store.trades["trade-1"]["contractItems"][0]
    assert created_item["id"] != "forged-id"
    assert created_item["id"] != "contract-1"
    assert created_item["name"] == "Roof"


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_nonexistent_contract_item_returns_safe_error(contract_client, method):
    _store, client = contract_client
    response = client.open(
        "/api/trades/trade-1/contract-items/missing-item",
        method=method,
        json={"name": "Roof", "amount": 500} if method == "PATCH" else None,
        headers=contract_headers(),
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "Contract item not found."}
