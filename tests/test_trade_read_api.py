from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from app import InMemoryFirestoreStore, app


USER_ID = "trade-reader-user"
SESSION_ID = "trade-reader-session"
TRADE_ID = "trade-reader-1"


@pytest.fixture
def trade_read_client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.save_user({
        "id": USER_ID,
        "username": "trade-reader",
        "role": "super_admin",
        "custom_staff_type": "",
        "password_hash": generate_password_hash("CurrentSecure1!"),
        "status": "active",
        "is_active": True,
        "must_change_password": False,
        "active_sessions": [SESSION_ID],
        "authorization_version": 3,
    })
    store.trades[TRADE_ID] = {
        "name": "MASONRY",
        "workerCategories": [{
            "id": "worker-1",
            "name": "Mason",
            "payment_type": "daily_wage",
            "currentWage": 700,
            "effective_from": "2026-10-01",
            "wageHistory": [{
                "dailyWage": 650,
                "effectiveFrom": "2026-09-01",
                "recordedAt": "2026-09-01T08:00:00Z",
                "unexpectedPrivateField": "must not leak",
            }],
            "work_description": "Wall work",
            "unapprovedWorkerField": "must not leak",
        }],
        "commonFields": ["Work / Activity", "Notes / Issues / Decisions"],
        "completedWorkValue": 12345,
        "contractItems": [{
            "id": "contract-1", "name": "Wall", "amount": 15000,
            "privateMemo": "must not leak",
        }],
        "paymentTransactions": [{
            "id": "payment-1", "type": "labour_settlement", "date": "2026-10-20",
            "amount": 800, "labourEarned": 1200, "advanceAdjustment": 100,
            "outstandingBalance": 400, "paymentMode": "cash", "remarks": "private",
            "createdAt": "2026-10-20T12:00:00Z", "workerCategoryId": "worker-1",
            "unexpectedPaymentField": "must not leak",
        }],
        "arbitrarySecret": "must not leak",
    }
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "trade-read-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["session_id"] = SESSION_ID
    return store, client


def assign_custom_role(store, client, role, permissions):
    user = store.get_user(USER_ID)
    user.update({"role": role, "custom_staff_type": role})
    store.save_user(user)
    store.save_custom_staff_type({"id": role.lower().replace(" ", "-"), "name": role, "permissions": permissions})
    with client.session_transaction() as flask_session:
        flask_session["role"] = role


def test_trade_builder_projection_uses_explicit_allowlist_and_hides_money(trade_read_client):
    store, client = trade_read_client
    assign_custom_role(store, client, "Site Engineer", ["trade_builder.read"])

    response = client.get("/api/trades?view=trade_builder")

    assert response.status_code == 200
    trade = response.get_json()["trades"][0]
    assert set(trade) == {"id", "name", "commonFields", "workerCategories"}
    worker = trade["workerCategories"][0]
    assert worker == {
        "id": "worker-1", "name": "Mason", "payment_type": "daily_wage",
        "effective_from": "2026-10-01",
    }
    assert "contractItems" not in trade
    assert "paymentTransactions" not in trade
    assert "arbitrarySecret" not in trade


def test_contract_values_permission_controls_contract_amount_projection(trade_read_client):
    store, client = trade_read_client
    assign_custom_role(store, client, "Contract Reviewer", ["trade_builder.read", "contract_values.edit"])

    response = client.get("/api/trades?view=trade_builder")

    assert response.status_code == 200
    trade = response.get_json()["trades"][0]
    assert trade["contractItems"] == [{"id": "contract-1", "name": "Wall", "amount": 15000}]
    assert trade["completedWorkValue"] == 12345
    assert "currentWage" not in trade["workerCategories"][0]
    assert "paymentTransactions" not in trade


def test_labour_payments_screen_marks_contract_card_unavailable_without_contract_permission(trade_read_client):
    store, client = trade_read_client
    assign_custom_role(store, client, "Office Accounts", ["labour_payments.read"])

    response = client.get("/labour-payments")

    assert response.status_code == 200
    assert b'data-can-read-contract-values="false"' in response.data


def test_labour_payment_projection_requires_permission_and_filters_nested_fields(trade_read_client):
    store, client = trade_read_client
    assign_custom_role(store, client, "Office Accounts", ["labour_payments.read"])

    response = client.get(f"/api/trades/{TRADE_ID}?view=labour_payments")

    assert response.status_code == 200
    trade = response.get_json()["trade"]
    assert trade["paymentTransactions"] == [{
        "id": "payment-1", "type": "labour_settlement", "date": "2026-10-20",
        "amount": 800, "labourEarned": 1200, "advanceAdjustment": 100,
        "outstandingBalance": 400, "paymentMode": "cash", "remarks": "private",
        "createdAt": "2026-10-20T12:00:00Z", "workerCategoryId": "worker-1",
    }]
    assert trade["workerCategories"][0]["currentWage"] == 700
    assert trade["workerCategories"][0]["wageHistory"] == [{
        "dailyWage": 650, "effectiveFrom": "2026-09-01", "recordedAt": "2026-09-01T08:00:00Z"
    }]
    assert "contractItems" not in trade
    assert "completedWorkValue" not in trade
    assert "arbitrarySecret" not in trade


def test_ledger_permission_returns_only_ledger_allowlisted_fields(trade_read_client):
    store, client = trade_read_client
    assign_custom_role(store, client, "Ledger Reader", ["ledger.read"])

    response = client.get("/api/trades?view=ledger")

    assert response.status_code == 200
    trade = response.get_json()["trades"][0]
    assert trade["contractItems"][0] == {
        "id": "contract-1", "name": "Wall", "amount": 15000
    }
    assert trade["paymentTransactions"][0]["amount"] == 800
    assert "unexpectedPaymentField" not in trade["paymentTransactions"][0]
    assert "arbitrarySecret" not in trade


def test_building_cost_permission_gets_only_amounts_needed_for_summary(trade_read_client):
    store, client = trade_read_client
    assign_custom_role(store, client, "Cost Reader", ["building_cost_summary.read"])

    response = client.get("/api/trades?view=building_cost_summary")

    assert response.status_code == 200
    trade = response.get_json()["trades"][0]
    assert set(trade) == {"id", "name", "paymentTransactions"}
    assert trade["paymentTransactions"] == [{
        "id": "payment-1", "type": "labour_settlement", "date": "2026-10-20", "amount": 800
    }]


def test_admin_role_name_does_not_grant_trade_read(trade_read_client):
    store, client = trade_read_client
    user = store.get_user(USER_ID)
    user.update({"role": "Admin", "custom_staff_type": ""})
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["role"] = "Admin"

    response = client.get("/api/trades?view=ledger")

    assert response.status_code == 403


@pytest.mark.parametrize("mutation", ["unauthenticated", "inactive", "revoked"])
def test_inactive_or_missing_flask_sessions_are_rejected(trade_read_client, mutation):
    store, client = trade_read_client
    if mutation == "unauthenticated":
        with client.session_transaction() as flask_session:
            flask_session.clear()
    elif mutation == "inactive":
        user = store.get_user(USER_ID)
        user["is_active"] = False
        store.save_user(user)
    else:
        user = store.get_user(USER_ID)
        user["active_sessions"] = []
        store.save_user(user)

    response = client.get("/api/trades?view=ledger")

    assert response.status_code == 401


def test_unknown_views_and_missing_permissions_fail_closed(trade_read_client):
    store, client = trade_read_client
    assign_custom_role(store, client, "Site Engineer", [])

    assert client.get("/api/trades?view=all").status_code == 400
    assert client.get("/api/trades?view=ledger").status_code == 403


def test_super_admin_can_read_all_supported_views(trade_read_client):
    _store, client = trade_read_client
    for view in ("trade_builder", "labour_payments", "ledger", "building_cost_summary"):
        response = client.get(f"/api/trades?view={view}")
        assert response.status_code == 200
        assert response.get_json()["trades"]


def test_daily_reports_trade_projection_and_rules_remain_scoped(trade_read_client):
    source = Path(__file__).parents[1] / "firestore.rules"
    rules = source.read_text(encoding="utf-8")
    daily_js = (Path(__file__).parents[1] / "static/daily_reports.js").read_text(encoding="utf-8")
    assert "match /daily_records/{recordId}" in rules
    assert "allow get, list: if hasPermission('daily_reports.read');" in rules
    assert "allow write: if false;" in rules
    assert 'collection(db, "daily_records")' in daily_js


def test_no_browser_module_reads_trade_documents_directly():
    root = Path(__file__).parents[1] / "static"
    for path in root.glob("*.js"):
        source = path.read_text(encoding="utf-8")
        assert 'collection(db, "trades")' not in source, path.name
        assert 'collection(db, TRADES_COLLECTION)' not in source, path.name
        assert 'doc(db, "trades"' not in source, path.name
