import copy
import uuid

import pytest
from werkzeug.security import generate_password_hash

from app import InMemoryFirestoreStore, app
from owner_scope import LEGACY_OWNER_ACCOUNT_DOCUMENT_ID


CSRF = "trade-builder-csrf-token"
SESSION_ID = "trade-builder-session"
USER_ID = "trade-builder-user"


@pytest.fixture
def trade_client(monkeypatch):
    store = InMemoryFirestoreStore()
    store.trades.update({
        "trade-1": {
            "name": "MASONRY",
            "workerCategories": [{
                "id": "worker-1",
                "name": "Mason",
                "payment_type": "daily_wage",
                "currentWage": 700,
                "effectiveFrom": "2026-10-01",
                "effective_from": "2026-10-01",
                "wageHistory": [{
                    "dailyWage": 700,
                    "effectiveFrom": "2026-10-01",
                    "recordedAt": "2026-10-01T08:00:00Z",
                }],
            }],
            "commonFields": [
                "Work / Activity",
                "Location / Floor",
                "Materials",
                "Notes / Issues / Decisions",
                "Remarks / Additional Items",
                "Visual Evidence",
            ],
            "completedWorkValue": 12000,
            "contractItems": [{"id": "contract-1", "name": "Wall", "amount": 15000}],
            "paymentTransactions": [{"id": "payment-1", "amount": 800}],
            "unrelatedField": {"preserve": True},
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-10-01T00:00:00Z",
        },
        "trade-2": {
            "name": "CARPENTRY",
            "contractItems": [{"id": "contract-2"}],
            "paymentTransactions": [{"id": "payment-2"}],
        },
        "d4f3ccce-65be-4b11-beca-6c5b9d06e657": {
            "name": "EXISTING",
            "workerCategories": [valid_worker(name="Existing worker")],
            "commonFields": [
                "Work / Activity",
                "Location / Floor",
                "Materials",
                "Notes / Issues / Decisions",
                "Remarks / Additional Items",
                "Visual Evidence",
            ],
            "contractItems": [{"id": "existing-contract"}],
            "paymentTransactions": [{"id": "existing-payment"}],
        },
        LEGACY_OWNER_ACCOUNT_DOCUMENT_ID: {
            "name": "OWNERS ACCOUNT",
            "contractItems": [],
            "paymentTransactions": [{"id": "legacy-payment"}],
        },
    })
    user = store.save_user({
        "id": USER_ID,
        "username": "trade-admin",
        "role": "super_admin",
        "custom_staff_type": "",
        "status": "active",
        "is_active": True,
        "active_sessions": [SESSION_ID],
        "authorization_version": 1,
        "password_hash": generate_password_hash("CurrentSecure1!"),
    })
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "trade-metadata-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user["id"]
        flask_session["session_id"] = SESSION_ID
        flask_session["trade_builder_csrf"] = CSRF
    return store, client


def csrf_headers():
    return {"X-CSRF-Token": CSRF}


def valid_worker(**overrides):
    worker = {
        "id": "worker-new",
        "name": "Mason",
        "payment_type": "daily_wage",
        "currentWage": 850,
        "effectiveFrom": "2026-10-07",
        "effective_from": "2026-10-07",
        "wageHistory": [{
            "dailyWage": 850,
            "effectiveFrom": "2026-10-07",
            "recordedAt": "2026-10-07T00:00:00Z",
        }],
    }
    worker.update(overrides)
    return worker


def trade_config(**overrides):
    payload = {
        "name": "MASONRY",
        "workerCategories": [valid_worker()],
        "commonFields": [
            "Work / Activity",
            "Location / Floor",
            "Materials",
            "Notes / Issues / Decisions",
            "Remarks / Additional Items",
            "Visual Evidence",
        ],
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
    with client.session_transaction() as flask_session:
        flask_session["role"] = role
    if permissions is not None:
        store.save_custom_staff_type({
            "id": "custom-role",
            "name": role,
            "permissions": permissions,
        })


def test_super_admin_can_create_trade_and_replay_same_create_safely(trade_client):
    store, client = trade_client
    trade_id = str(uuid.uuid4())
    payload = {"id": trade_id, **trade_config()}

    created = client.post("/api/trades", json=payload, headers=csrf_headers())
    retried = client.post("/api/trades", json=payload, headers=csrf_headers())

    assert created.status_code == 201
    assert retried.status_code == 200
    trade = store.trades[trade_id]
    assert trade["contractItems"] == []
    assert trade["paymentTransactions"] == []
    assert trade["name"] == "MASONRY"
    assert trade["createdAt"] == trade["updatedAt"]
    assert len(store.trades) == 5


def test_created_trade_document_id_round_trips_through_edit_and_delete(trade_client):
    store, client = trade_client
    trade_id = str(uuid.uuid4())
    created = client.post(
        "/api/trades",
        json={"id": trade_id, **trade_config()},
        headers=csrf_headers(),
    )
    assert created.status_code == 201
    assert created.get_json()["id"] == trade_id

    listed_trade = next(item for item in store.list_trades() if item["id"] == trade_id)
    updated = client.patch(
        f"/api/trades/{listed_trade['id']}",
        json=trade_config(
            name="Updated Masonry",
            workerCategories=[valid_worker(name="Senior Mason", currentWage=950)],
        ),
        headers=csrf_headers(),
    )
    assert updated.status_code == 200
    assert store.trades[trade_id]["name"] == "UPDATED MASONRY"
    assert store.trades[trade_id]["workerCategories"][0]["currentWage"] == 950

    deleted = client.delete(
        f"/api/trades/{listed_trade['id']}", headers=csrf_headers()
    )
    assert deleted.status_code == 200
    assert all(item["id"] != trade_id for item in store.list_trades())


def test_create_conflict_does_not_reinitialize_existing_trade(trade_client):
    store, client = trade_client
    existing_id = "d4f3ccce-65be-4b11-beca-6c5b9d06e657"
    before = copy.deepcopy(store.trades[existing_id])
    response = client.post(
        "/api/trades",
        json={"id": existing_id, **trade_config()},
        headers=csrf_headers(),
    )
    assert response.status_code == 409
    assert store.trades[existing_id] == before


def test_trade_edit_updates_configuration_only_and_preserves_financial_and_unrelated_fields(
    trade_client,
):
    store, client = trade_client
    before = copy.deepcopy(store.trades["trade-1"])
    payload = trade_config(
        name="Updated Masonry",
        workerCategories=[valid_worker(name="Senior Mason", currentWage=950)],
        completedWorkValue=17000,
    )

    response = client.patch(
        "/api/trades/trade-1", json=payload, headers=csrf_headers()
    )

    assert response.status_code == 200
    updated = store.trades["trade-1"]
    assert updated["name"] == "UPDATED MASONRY"
    assert updated["workerCategories"][0]["currentWage"] == 950
    assert updated["commonFields"] == before["commonFields"]
    assert updated["completedWorkValue"] == 17000
    assert updated["contractItems"] == before["contractItems"]
    assert updated["paymentTransactions"] == before["paymentTransactions"]
    assert updated["createdAt"] == before["createdAt"]
    assert updated["unrelatedField"] == before["unrelatedField"]
    assert set(updated) == set(before)


def test_trade_edit_rejects_financial_or_arbitrary_fields(trade_client):
    store, client = trade_client
    before = copy.deepcopy(store.trades["trade-1"])
    invalid = trade_config(contractItems=[], paymentTransactions=[], role="super_admin")

    response = client.patch(
        "/api/trades/trade-1", json=invalid, headers=csrf_headers()
    )

    assert response.status_code == 400
    assert store.trades["trade-1"] == before


def test_trade_delete_removes_only_document_and_does_not_cascade(trade_client):
    store, client = trade_client
    before_other = copy.deepcopy(store.trades["trade-2"])
    before_legacy = copy.deepcopy(store.trades[LEGACY_OWNER_ACCOUNT_DOCUMENT_ID])

    response = client.delete("/api/trades/trade-1", headers=csrf_headers())

    assert response.status_code == 200
    assert "trade-1" not in store.trades
    assert store.trades["trade-2"] == before_other
    assert store.trades[LEGACY_OWNER_ACCOUNT_DOCUMENT_ID] == before_legacy


def test_legacy_owner_account_trade_cannot_be_edited_renamed_or_deleted(trade_client):
    store, client = trade_client
    before = copy.deepcopy(store.trades[LEGACY_OWNER_ACCOUNT_DOCUMENT_ID])
    payload = trade_config()

    edit = client.patch(
        f"/api/trades/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}",
        json=payload,
        headers=csrf_headers(),
    )
    rename = client.patch(
        f"/api/trades/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}/name",
        json={"name": "Changed"},
        headers=csrf_headers(),
    )
    delete = client.delete(
        f"/api/trades/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}",
        headers=csrf_headers(),
    )

    assert [edit.status_code, rename.status_code, delete.status_code] == [400, 400, 400]
    assert store.trades[LEGACY_OWNER_ACCOUNT_DOCUMENT_ID] == before


def test_ledger_rename_changes_only_name_and_updated_at(trade_client):
    store, client = trade_client
    before = copy.deepcopy(store.trades["trade-1"])

    response = client.patch(
        "/api/trades/trade-1/name",
        json={"name": "New Trade Name"},
        headers=csrf_headers(),
    )

    assert response.status_code == 200
    assert response.get_json()["name"] == "NEW TRADE NAME"
    updated = store.trades["trade-1"]
    assert updated["name"] == "NEW TRADE NAME"
    assert updated["updatedAt"] != before["updatedAt"]
    assert {key: value for key, value in updated.items() if key not in {"name", "updatedAt"}} == {
        key: value for key, value in before.items() if key not in {"name", "updatedAt"}
    }


def test_ledger_rename_rejects_attempt_to_change_financial_or_trade_fields(trade_client):
    store, client = trade_client
    before = copy.deepcopy(store.trades["trade-1"])
    response = client.patch(
        "/api/trades/trade-1/name",
        json={"name": "Changed", "paymentTransactions": [], "contractItems": []},
        headers=csrf_headers(),
    )
    assert response.status_code == 400
    assert store.trades["trade-1"] == before


@pytest.mark.parametrize("role", ["office_staff", "supervisor", "Unconfigured Custom"])
@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "NAME"])
def test_non_super_admin_roles_cannot_mutate_trade_metadata(trade_client, role, method):
    store, client = trade_client
    set_role(store, client, role)
    if method == "POST":
        response = client.post(
            "/api/trades",
            json={"id": str(uuid.uuid4()), **trade_config()},
            headers=csrf_headers(),
        )
    elif method == "PATCH":
        response = client.patch(
            "/api/trades/trade-1",
            json=trade_config(),
            headers=csrf_headers(),
        )
    elif method == "DELETE":
        response = client.delete("/api/trades/trade-1", headers=csrf_headers())
    else:
        response = client.patch(
            "/api/trades/trade-1/name",
            json={"name": "Changed"},
            headers=csrf_headers(),
        )
    assert response.status_code == 403


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "NAME"])
def test_trade_mutations_reject_unauthenticated_requests(trade_client, method):
    _store, client = trade_client
    with client.session_transaction() as flask_session:
        flask_session.clear()
    if method == "POST":
        response = client.post(
            "/api/trades",
            json={"id": str(uuid.uuid4()), **trade_config()},
            headers=csrf_headers(),
        )
    elif method == "PATCH":
        response = client.patch(
            "/api/trades/trade-1", json=trade_config(), headers=csrf_headers()
        )
    elif method == "DELETE":
        response = client.delete("/api/trades/trade-1", headers=csrf_headers())
    else:
        response = client.patch(
            "/api/trades/trade-1/name",
            json={"name": "Changed"},
            headers=csrf_headers(),
        )
    assert response.status_code == 401


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "NAME"])
def test_trade_mutations_reject_missing_csrf(trade_client, method):
    _store, client = trade_client
    if method == "POST":
        response = client.post(
            "/api/trades",
            json={"id": str(uuid.uuid4()), **trade_config()},
        )
    elif method == "PATCH":
        response = client.patch("/api/trades/trade-1", json=trade_config())
    elif method == "DELETE":
        response = client.delete("/api/trades/trade-1")
    else:
        response = client.patch(
            "/api/trades/trade-1/name", json={"name": "Changed"}
        )
    assert response.status_code == 403


def test_forged_session_authorization_fields_do_not_elevate_role(trade_client):
    store, client = trade_client
    user = store.get_user(USER_ID)
    user["role"] = "office_staff"
    store.save_user(user)
    with client.session_transaction() as flask_session:
        flask_session["role"] = "super_admin"
        flask_session["permissions"] = ["trade_builder.write", "trade_builder.delete"]

    response = client.delete(
        "/api/trades/trade-1",
        json={
            "role": "super_admin",
            "permissions": ["trade_builder.delete"],
            "user_id": "another-user",
            "is_super_admin": True,
        },
        headers=csrf_headers(),
    )

    assert response.status_code == 403
    assert "trade-1" in store.trades


@pytest.mark.parametrize("status", ["inactive", "forced-password-change"])
def test_trade_mutations_reject_inactive_or_forced_password_change_user(
    trade_client, status
):
    store, client = trade_client
    user = store.get_user(USER_ID)
    if status == "inactive":
        user["status"] = "disabled"
        user["is_active"] = False
    else:
        user["must_change_password"] = True
    store.save_user(user)
    response = client.delete("/api/trades/trade-1", headers=csrf_headers())
    assert response.status_code == (401 if status == "inactive" else 403)
    assert "trade-1" in store.trades


@pytest.mark.parametrize(
    "worker",
    [
        {"id": "worker-1", "name": "Mason", "payment_type": "daily_wage", "currentWage": -1},
        {"id": "worker-1", "name": "Mason", "payment_type": "daily_wage", "currentWage": float("inf")},
        {"id": "worker-1", "name": "Mason", "payment_type": "invalid"},
    ],
)
def test_trade_configuration_rejects_invalid_worker_values_without_changes(
    trade_client, worker
):
    store, client = trade_client
    before = copy.deepcopy(store.trades["trade-1"])
    response = client.patch(
        "/api/trades/trade-1",
        json=trade_config(workerCategories=[worker]),
        headers=csrf_headers(),
    )
    assert response.status_code == 400
    assert store.trades["trade-1"] == before


def test_trade_api_rejects_invalid_and_nonexistent_ids(trade_client):
    _store, client = trade_client
    invalid = client.delete("/api/trades/../invalid", headers=csrf_headers())
    missing = client.delete("/api/trades/missing-trade", headers=csrf_headers())
    assert invalid.status_code in {400, 404}
    assert missing.status_code == 404
