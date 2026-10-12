import copy

import pytest
from werkzeug.security import generate_password_hash

from app import InMemoryFirestoreStore, app


@pytest.fixture
def payment_client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.trades["trade-1"] = {
        "name": "Masonry",
        "contractTotal": 50000,
        "workerCategories": [
            {
                "id": "worker-1",
                "name": "Mason",
                "payment_type": "lump_sum",
                "lump_sum_amount": 1500,
            }
        ],
        "contractItems": [{"id": "contract-1", "name": "Wall", "amount": 50000}],
        "paymentTransactions": [
            {
                "id": "payment-existing",
                "type": "labour_advance",
                "date": "2026-10-01",
                "amount": 1000,
                "labourEarned": 0,
                "advanceAdjustment": 0,
                "outstandingBalance": 0,
                "paymentMode": "Cash",
                "remarks": "Existing advance",
                "createdAt": "2026-10-01T08:00:00Z",
                "updatedAt": "2026-10-01T08:00:00Z",
                "legacyAuditField": "keep",
            },
            {
                "id": "payment-other",
                "type": "mobilization_advance",
                "date": "2026-10-02",
                "amount": 200,
                "paymentMode": "UPI",
                "remarks": "Other",
                "createdAt": "2026-10-02T08:00:00Z",
                "updatedAt": "2026-10-02T08:00:00Z",
            },
        ],
        "unrelatedField": {"preserve": True},
        "updatedAt": "2026-10-01T08:00:00Z",
    }
    user = store.save_user({
        "id": "user-1",
        "username": "user-1",
        "role": "super_admin",
        "custom_staff_type": "",
        "status": "active",
        "is_active": True,
        "active_sessions": ["session-1"],
        "permissions": [],
        "password_hash": generate_password_hash("CurrentSecure1!"),
        "authorization_version": 2,
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "payment-api-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user["id"]
        flask_session["role"] = user["role"]
        flask_session["session_id"] = "session-1"
        flask_session["labour_payments_csrf"] = "payments-csrf-token"
    return store, client


def payment_payload(**overrides):
    payload = {
        "type": "labour_advance",
        "date": "2026-10-03",
        "amount": 250,
        "labourEarned": 0,
        "advanceAdjustmentMode": "none",
        "requestedAdjustment": 0,
        "paymentMode": "Bank Transfer",
        "remarks": "Work payment",
    }
    payload.update(overrides)
    return payload


def post_create(client, *, request_id=None, **payload_fields):
    headers = {"X-CSRF-Token": "payments-csrf-token"}
    if request_id:
        headers["Idempotency-Key"] = request_id
    return client.post(
        "/api/labour-payments/trade-1/transactions",
        json=payment_payload(**payload_fields),
        headers=headers,
    )


def set_role(store, client, role, *, custom_permissions=None):
    user = store.get_user("user-1")
    user["role"] = role
    user["custom_staff_type"] = role if role not in {"super_admin", "office_staff", "supervisor"} else ""
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["role"] = role
    if custom_permissions is not None:
        store.save_custom_staff_type({
            "id": "custom-1",
            "name": role,
            "permissions": custom_permissions,
        })


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_unauthenticated_mutations_are_rejected(payment_client, method):
    _store, client = payment_client
    with client.session_transaction() as flask_session:
        flask_session.clear()
    route = "/api/labour-payments/trade-1/transactions"
    if method != "POST":
        route += "/payment-existing"

    response = client.open(
        route,
        method=method,
        json=payment_payload() if method != "DELETE" else None,
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )

    assert response.status_code == 401


@pytest.mark.parametrize("role", ["office_staff", "supervisor"])
def test_office_staff_and_supervisor_can_create_payments(payment_client, role):
    store, client = payment_client
    set_role(store, client, role)

    response = post_create(client)

    assert response.status_code == 201
    saved = store.trades["trade-1"]["paymentTransactions"]
    assert len(saved) == 3
    created = saved[-1]
    assert created["amount"] == 250
    assert created["type"] == "labour_advance"
    assert created["id"]
    assert created["id"] != "client-controlled-id"
    assert response.get_json()["paymentTransactions"] == saved


def test_repeated_create_request_is_idempotent_and_does_not_duplicate_payment(payment_client):
    store, client = payment_client
    request_id = "26b37da0-b542-4d48-87a1-209217e0508c"

    first = post_create(client, request_id=request_id)
    retry = post_create(client, request_id=request_id)

    assert first.status_code == 201
    assert retry.status_code == 201
    assert first.get_json()["transaction"]["id"] == request_id
    assert retry.get_json()["transaction"]["id"] == request_id
    assert len(store.trades["trade-1"]["paymentTransactions"]) == 3

    collision = post_create(client, request_id=request_id, amount=999)
    assert collision.status_code == 409
    assert len(store.trades["trade-1"]["paymentTransactions"]) == 3


def test_custom_role_needs_explicit_create_permission(payment_client):
    store, client = payment_client
    set_role(store, client, "Payments Operator")

    denied = post_create(client)
    assert denied.status_code == 403

    set_role(store, client, "Payments Operator", custom_permissions=["labour_payments.create"])
    allowed = post_create(client)
    assert allowed.status_code == 201


@pytest.mark.parametrize(
    ("method", "permission"),
    [("PATCH", "labour_payments.edit"), ("DELETE", "labour_payments.delete")],
)
def test_custom_role_can_mutate_only_with_matching_explicit_permission(payment_client, method, permission):
    store, client = payment_client
    set_role(store, client, "Payment Maintainer", custom_permissions=[permission])
    response = client.open(
        "/api/labour-payments/trade-1/transactions/payment-existing",
        method=method,
        json=payment_payload(amount=300) if method == "PATCH" else None,
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("method", "permission"),
    [("PATCH", "labour_payments.edit"), ("DELETE", "labour_payments.delete")],
)
def test_office_staff_cannot_edit_or_delete_without_explicit_permission(payment_client, method, permission):
    store, client = payment_client
    set_role(store, client, "office_staff")

    response = client.open(
        "/api/labour-payments/trade-1/transactions/payment-existing",
        method=method,
        json=payment_payload() if method == "PATCH" else None,
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )

    assert response.status_code == 403
    assert permission


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_mutations_require_csrf(payment_client, method):
    _store, client = payment_client
    route = "/api/labour-payments/trade-1/transactions"
    if method != "POST":
        route += "/payment-existing"

    response = client.open(
        route,
        method=method,
        json=payment_payload() if method != "DELETE" else None,
    )

    assert response.status_code == 403


def test_super_admin_can_create_edit_and_delete_payment(payment_client):
    store, client = payment_client
    created = post_create(client)
    assert created.status_code == 201
    payment_id = created.get_json()["transaction"]["id"]
    original_created_at = created.get_json()["transaction"]["createdAt"]

    updated = client.patch(
        f"/api/labour-payments/trade-1/transactions/{payment_id}",
        json=payment_payload(amount=275, remarks="Updated"),
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )
    assert updated.status_code == 200
    edited = updated.get_json()["transaction"]
    assert edited["id"] == payment_id
    assert edited["createdAt"] == original_created_at
    assert edited["amount"] == 275
    assert edited["remarks"] == "Updated"

    deleted = client.delete(
        f"/api/labour-payments/trade-1/transactions/{payment_id}",
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )
    assert deleted.status_code == 200
    assert all(item["id"] != payment_id for item in store.trades["trade-1"]["paymentTransactions"])


def test_browser_cannot_forge_role_permissions_user_or_payment_id(payment_client):
    store, client = payment_client
    with client.session_transaction() as flask_session:
        flask_session["role"] = "office_staff"

    response = post_create(
        client,
        role="super_admin",
        permissions=["labour_payments.edit", "labour_payments.delete"],
        user_id="admin-user",
        id="forged-payment-id",
    )

    assert response.status_code == 400
    assert len(store.trades["trade-1"]["paymentTransactions"]) == 2


def test_nonexistent_trade_returns_safe_not_found(payment_client):
    _store, client = payment_client

    response = client.post(
        "/api/labour-payments/missing-trade/transactions",
        json=payment_payload(),
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "Trade not found."}


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_nonexistent_payment_returns_not_found(payment_client, method):
    _store, client = payment_client
    response = client.open(
        "/api/labour-payments/trade-1/transactions/missing-payment",
        method=method,
        json=payment_payload() if method == "PATCH" else None,
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "Payment transaction not found."}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"amount": -1}, "Enter a valid payment amount greater than zero."),
        ({"amount": "not-a-number"}, "Payment amount must be a valid number."),
        ({"date": "10/03/2026"}, "Enter a valid payment date."),
        ({"paymentMode": "Crypto"}, "Select a valid payment mode."),
        ({"amount": 0}, "Enter a valid payment amount greater than zero."),
    ],
)
def test_invalid_financial_fields_are_rejected(payment_client, overrides, expected):
    store, client = payment_client
    before = copy.deepcopy(store.trades["trade-1"])

    response = post_create(client, **overrides)

    assert response.status_code == 400
    assert response.get_json()["error"] == expected
    assert store.trades["trade-1"] == before


def test_settlement_values_are_calculated_server_side(payment_client):
    store, client = payment_client

    response = post_create(
        client,
        type="labour_settlement",
        amount=300,
        labourEarned=500,
        advanceAdjustmentMode="partial",
        requestedAdjustment=200,
        advanceAdjustment=999999,
        outstandingBalance=999999,
    )

    assert response.status_code == 400
    assert len(store.trades["trade-1"]["paymentTransactions"]) == 2

    valid_response = client.post(
        "/api/labour-payments/trade-1/transactions",
        json=payment_payload(
            type="labour_settlement",
            amount=300,
            labourEarned=500,
            advanceAdjustmentMode="partial",
            requestedAdjustment=200,
        ),
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )
    assert valid_response.status_code == 201
    settlement = valid_response.get_json()["transaction"]
    assert settlement["advanceAdjustment"] == 200
    assert settlement["outstandingBalance"] == 0


def test_zero_labour_advance_balance_saves_no_stale_partial_deduction(payment_client):
    store, client = payment_client
    store.trades["trade-1"]["paymentTransactions"] = [
        transaction
        for transaction in store.trades["trade-1"]["paymentTransactions"]
        if transaction["type"] == "mobilization_advance"
    ]

    response = post_create(
        client,
        type="labour_settlement",
        amount=25000,
        labourEarned=25000,
        advanceAdjustmentMode="partial",
        requestedAdjustment=2500,
    )

    assert response.status_code == 201
    settlement = response.get_json()["transaction"]
    assert settlement["advanceAdjustment"] == 0
    assert settlement["amount"] == 25000


def test_lump_sum_create_is_checked_against_server_agreement_balance(payment_client):
    store, client = payment_client
    response = post_create(
        client,
        type="lump_sum_payment",
        amount=1501,
        workerCategoryId="worker-1",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Payment exceeds the remaining lump-sum balance."
    assert len(store.trades["trade-1"]["paymentTransactions"]) == 2


def test_edit_preserves_id_other_transactions_and_unrelated_trade_data(payment_client):
    store, client = payment_client
    before = copy.deepcopy(store.trades["trade-1"])

    response = client.patch(
        "/api/labour-payments/trade-1/transactions/payment-existing",
        json=payment_payload(amount=400),
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )

    assert response.status_code == 200
    updated = store.trades["trade-1"]
    assert updated["paymentTransactions"][0]["id"] == "payment-existing"
    assert updated["paymentTransactions"][0]["legacyAuditField"] == "keep"
    assert updated["paymentTransactions"][0]["amount"] == 400
    assert updated["paymentTransactions"][1] == before["paymentTransactions"][1]
    for field in ("name", "contractTotal", "workerCategories", "contractItems", "unrelatedField"):
        assert updated[field] == before[field]


def test_delete_preserves_other_payments_and_trade_fields(payment_client):
    store, client = payment_client
    before = copy.deepcopy(store.trades["trade-1"])

    response = client.delete(
        "/api/labour-payments/trade-1/transactions/payment-existing",
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )

    assert response.status_code == 200
    updated = store.trades["trade-1"]
    assert updated["paymentTransactions"] == [before["paymentTransactions"][1]]
    for field in ("name", "contractTotal", "workerCategories", "contractItems", "unrelatedField"):
        assert updated[field] == before[field]


def test_deleting_earlier_settlement_recalculates_later_pending_balance(payment_client):
    store, client = payment_client
    store.trades["trade-1"]["paymentTransactions"] = [
        {
            "id": "settlement-earlier",
            "type": "labour_settlement",
            "date": "2026-10-01",
            "amount": 10000,
            "labourEarned": 20000,
            "advanceAdjustment": 0,
            "outstandingBalance": 10000,
        },
        {
            "id": "settlement-later",
            "type": "labour_settlement",
            "date": "2026-10-02",
            "amount": 20000,
            "labourEarned": 20000,
            "advanceAdjustment": 0,
            "outstandingBalance": 10000,
        },
    ]

    response = client.delete(
        "/api/labour-payments/trade-1/transactions/settlement-earlier",
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )

    assert response.status_code == 200
    remaining = store.trades["trade-1"]["paymentTransactions"]
    assert [item["id"] for item in remaining] == ["settlement-later"]
    assert remaining[0]["outstandingBalance"] == 0


def test_editing_earlier_settlement_recalculates_later_pending_balance(payment_client):
    store, client = payment_client
    store.trades["trade-1"]["paymentTransactions"] = [
        {
            "id": "settlement-earlier",
            "type": "labour_settlement",
            "date": "2026-10-01",
            "amount": 10000,
            "labourEarned": 20000,
            "advanceAdjustment": 0,
            "outstandingBalance": 10000,
            "createdAt": "2026-10-01T08:00:00Z",
        },
        {
            "id": "settlement-later",
            "type": "labour_settlement",
            "date": "2026-10-02",
            "amount": 20000,
            "labourEarned": 20000,
            "advanceAdjustment": 0,
            "outstandingBalance": 10000,
            "createdAt": "2026-10-02T08:00:00Z",
        },
    ]

    response = client.patch(
        "/api/labour-payments/trade-1/transactions/settlement-earlier",
        json=payment_payload(
            type="labour_settlement",
            date="2026-10-01",
            amount=20000,
            labourEarned=20000,
        ),
        headers={"X-CSRF-Token": "payments-csrf-token"},
    )

    assert response.status_code == 200
    remaining = store.trades["trade-1"]["paymentTransactions"]
    assert [item["outstandingBalance"] for item in remaining] == [0, 0]
