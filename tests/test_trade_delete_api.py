import uuid

import pytest

import app as app_module


@pytest.fixture
def trade_delete_client(monkeypatch):
    store = app_module.InMemoryFirestoreStore()
    permission = {"allowed": True}
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    monkeypatch.setitem(app_module.app.config, "SECRET_KEY", "trade-delete-test-secret")
    monkeypatch.setattr(app_module, "get_user_store", lambda: store)
    monkeypatch.setattr(
        app_module,
        "get_current_user",
        lambda: {"id": "test-admin", "must_change_password": False},
    )
    monkeypatch.setattr(
        app_module,
        "_user_has_compiled_permission",
        lambda _user, permission_name: (
            permission["allowed"] and permission_name == "trade_builder.delete"
        ),
    )

    with app_module.app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["trade_builder_csrf"] = "trade-delete-test-csrf"
        yield client, store, permission


def _delete(client, trade_id):
    return client.delete(
        f"/api/trades/{trade_id}",
        headers={"X-CSRF-Token": "trade-delete-test-csrf"},
    )


def test_delete_trade_removes_the_trade_by_document_id_and_refreshes_store(
    trade_delete_client,
):
    client, store, _permission = trade_delete_client
    trade_id = str(uuid.uuid4())
    other_trade_id = str(uuid.uuid4())
    store.trades[trade_id] = {"name": "Concrete"}
    store.trades[other_trade_id] = {"name": "Steel"}

    response = _delete(client, trade_id)

    assert response.status_code == 200
    assert response.get_json() == {"deleted": True}
    assert [trade["id"] for trade in store.list_trades()] == [other_trade_id]


def test_delete_trade_rejects_invalid_document_id_without_mutating_store(
    trade_delete_client,
):
    client, store, _permission = trade_delete_client
    trade_id = str(uuid.uuid4())
    store.trades[trade_id] = {"name": "Concrete"}

    response = _delete(client, ".")

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid trade ID."}
    assert trade_id in store.trades


def test_delete_trade_reports_missing_document(trade_delete_client):
    client, store, _permission = trade_delete_client
    trade_id = str(uuid.uuid4())

    response = _delete(client, trade_id)

    assert response.status_code == 404
    assert response.get_json() == {"error": "Trade not found."}
    assert store.trades == {}


def test_delete_trade_denies_users_without_delete_permission(trade_delete_client):
    client, store, permission = trade_delete_client
    trade_id = str(uuid.uuid4())
    store.trades[trade_id] = {"name": "Concrete"}
    permission["allowed"] = False

    response = _delete(client, trade_id)

    assert response.status_code == 403
    assert response.get_json() == {
        "error": "You do not have permission to perform this Trade Builder action."
    }
    assert trade_id in store.trades


def test_delete_trade_requires_csrf_token(trade_delete_client):
    client, store, _permission = trade_delete_client
    trade_id = str(uuid.uuid4())
    store.trades[trade_id] = {"name": "Concrete"}

    response = client.delete(f"/api/trades/{trade_id}")

    assert response.status_code == 403
    assert response.get_json() == {
        "error": "Your session expired. Reload the page and try again."
    }
    assert trade_id in store.trades


def test_delete_trade_reports_a_delete_specific_error_on_store_failure(
    trade_delete_client, caplog
):
    client, store, _permission = trade_delete_client
    trade_id = str(uuid.uuid4())
    store.trades[trade_id] = {"name": "Concrete"}

    def fail_delete(_trade_id):
        raise RuntimeError("simulated Firestore permission failure")

    store.delete_trade = fail_delete

    response = _delete(client, trade_id)

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "Unable to delete the trade. Please try again."
    }
    assert "Trade delete failed (RuntimeError)." in caplog.text
    assert trade_id in store.trades
