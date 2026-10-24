from pathlib import Path

import pytest

from app import app

ROOT = Path(__file__).parents[1]


class FakeUserStore:
    def get_user(self, user_id):
        return {
            "id": user_id,
            "username": "cost-summary-test",
            "role": "office_staff",
            "active_sessions": ["cost-summary-session"],
        }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "building-cost-summary-test")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", FakeUserStore())
    test_client = app.test_client()
    with test_client.session_transaction() as session:
        session["user_id"] = "cost-summary-test"
        session["session_id"] = "cost-summary-session"
    return test_client


def test_building_cost_summary_route_is_login_protected_and_renders_module(client):
    unauthenticated = app.test_client().get("/building-cost-summary")
    assert unauthenticated.status_code == 401
    assert b"You need to sign in to view this page." in unauthenticated.data
    response = client.get("/building-cost-summary")
    assert response.status_code == 200
    assert b"TOTAL BUILDING COST TILL DATE" in response.data
    assert b"Material Cost Breakdown" in response.data
    assert b"TOTAL LABOUR COST" in response.data
    assert b"OWNER SCOPE COST" in response.data
    assert b"ownerScopeTotal" in response.data
    assert b'id="includeOwnerScope" type="checkbox" checked' in response.data
    assert b"Include Owner Scope in Total Building Cost" in response.data
    assert "â‚¹".encode("utf-8") not in response.data
    assert b"Labour Cost Breakdown" in response.data
    assert b"TOTAL TRADE COST" not in response.data
    assert b"Trade Cost Breakdown" not in response.data
    assert b"building_cost_summary.js" in response.data


def test_dashboard_links_to_building_cost_summary():
    dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    assert 'href="/building-cost-summary"' in dashboard
    assert "Building Cost Summary" in dashboard


def test_summary_reads_existing_data_and_never_writes_firestore():
    source = (ROOT / "static/building_cost_summary.js").read_text(encoding="utf-8")
    assert 'subscribe("material_categories"' in source
    assert 'subscribe("material_purchases"' in source
    assert 'fetch("/api/trades?view=building_cost_summary"' in source
    assert 'subscribe("trades"' not in source
    assert 'doc(db, "sync_metadata", "trade-projection")' in source
    assert 'window.setInterval(refreshTrades, 60000)' not in source
    assert 'document.addEventListener("visibilitychange"' not in source
    assert 'fetch(`/api/trades/${encodeURIComponent(tradeId)}?view=building_cost_summary`' in source
    assert 'unsubscribeListeners.forEach(unsubscribe => unsubscribe?.());' in source
    assert 'collection(db, OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID, "payments")' in source
    assert "ownerScopePayments" in source
    assert "onSnapshot(" in source
    assert "updateDoc(" not in source
    assert "addDoc(" not in source
    assert "deleteDoc(" not in source


def test_payment_collection_and_actual_amount_fields_are_explicit():
    logic = (ROOT / "static/building_cost_summary_logic.mjs").read_text(encoding="utf-8")
    for payment_type in (
        "mobilization_advance", "labour_advance", "labour_settlement",
        "completed_work_payment", "lump_sum_payment",
    ):
        assert f'"{payment_type}"' in logic
    assert "transaction?.amount" in logic
    assert "labourEarned" not in logic
    assert "contractItems" not in logic
    assert "ownerScopeTotal" in logic


def test_summary_displays_rupees_without_decimal_places():
    logic = (ROOT / "static/building_cost_summary_logic.mjs").read_text(encoding="utf-8")
    page = (ROOT / "static/building_cost_summary.js").read_text(encoding="utf-8")
    assert "maximumFractionDigits: 0" in logic
    assert "minimumFractionDigits: 0" in logic
    assert page.count("formatRupees(") >= 4


def test_owner_scope_toggle_only_controls_its_contribution_to_building_total():
    page = (ROOT / "static/building_cost_summary.js").read_text(encoding="utf-8")
    logic = (ROOT / "static/building_cost_summary_logic.mjs").read_text(encoding="utf-8")
    assert '$("includeOwnerScope").addEventListener("change", render);' in page
    assert 'includeOwnerScope: $("includeOwnerScope").checked' in page
    assert "ownerScopeTotal: formatRupees(result.ownerScopeTotal)" not in page
    assert '$("ownerScopeTotal").textContent = formatRupees(result.ownerScopeTotal);' in page
    assert "(includeOwnerScope ? ownerScopeTotal : 0)" in logic