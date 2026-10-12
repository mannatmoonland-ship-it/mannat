from pathlib import Path
import pytest

from app import app


class TradeBuilderUserStore:
    def __init__(self, role):
        self.user = {
            "id": "trade-builder-test-user",
            "username": "trade-builder-user",
            "full_name": "Trade Builder User",
            "role": role,
            "active_sessions": ["trade-builder-test-session"],
        }

    def get_user(self, user_id):
        return self.user if user_id == self.user["id"] else None

    def list_custom_staff_types(self):
        return []


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("super_admin", True), ("office_staff", False), ("supervisor", False), ("custom_staff", False)],
)
def test_trade_builder_module_is_super_admin_only(monkeypatch, role, allowed):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", TradeBuilderUserStore(role))

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "trade-builder-test-user"
            session["session_id"] = "trade-builder-test-session"

        response = client.get("/trade-builder")
        dashboard = client.get("/") if allowed else None

    if not allowed:
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
        return

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert dashboard is not None and b'href="/trade-builder"' in dashboard.data
    assert b"OPEN TRADE BUILDER" in dashboard.data
    for control in (
        'id="saveTradeBtn"',
        'id="tradeName"',
        'id="addWorkerBtn"',
        'id="workerContainer"',
        'id="tradeList"',
        'id="paymentType"',
        'id="tradeLedgerModal"',
    ):
        assert control in html
    assert "Common Fields" in html


@pytest.mark.parametrize("role", ["office_staff", "supervisor", "custom_staff"])
def test_non_admin_navigation_hides_trade_builder_but_other_modules_remain(monkeypatch, role):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", TradeBuilderUserStore(role))

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = "trade-builder-test-user"
            session["session_id"] = "trade-builder-test-session"

        dashboard = client.get("/")
        labour_payments = client.get("/labour-payments")

    assert dashboard.status_code == 200
    assert b'href="/trade-builder"' not in dashboard.data
    assert labour_payments.status_code == 200