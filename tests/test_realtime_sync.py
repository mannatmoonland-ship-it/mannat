from pathlib import Path

from flask import request

import app as app_module


ROOT = Path(__file__).parents[1]


def test_trade_revision_notifies_only_the_changed_trade_when_possible(monkeypatch):
    published = []
    monkeypatch.setattr(
        app_module,
        "_publish_trade_projection_revision",
        lambda **options: published.append(options),
    )
    with app_module.app.test_request_context("/api/trades/trade-42", method="PATCH"):
        request.view_args = {"trade_id": "trade-42"}
        response = app_module.notify_trade_projection_clients(
            app_module.app.response_class(status=200)
        )

    assert response.status_code == 200
    assert published == [{"trade_ids": ["trade-42"], "refresh_all": False}]


def test_trade_order_revision_requests_a_filtered_list_refresh(monkeypatch):
    published = []
    monkeypatch.setattr(
        app_module,
        "_publish_trade_projection_revision",
        lambda **options: published.append(options),
    )
    with app_module.app.test_request_context(
        "/api/trades/order",
        method="PUT",
        json={"tradeIds": ["trade-2", "trade-1"]},
    ):
        response = app_module.notify_trade_projection_clients(
            app_module.app.response_class(status=200)
        )

    assert response.status_code == 200
    assert published == [{"trade_ids": [], "refresh_all": True}]


def test_business_data_modules_use_snapshots_without_refresh_timers():
    files = [
        "app.js",
        "building_cost_summary.js",
        "daily_report_summary.js",
        "daily_reports.js",
        "dashboard_pinned.js",
        "drawings.js",
        "ledger_prints.js",
        "materials.js",
        "owner_scope.js",
        "red_book.js",
        "red_book_archive.js",
    ]
    for filename in files:
        source = (ROOT / "static" / filename).read_text(encoding="utf-8")
        assert "onSnapshot" in source, filename
        assert "setInterval(" not in source, filename

    assert "visibilitychange" not in (
        ROOT / "static" / "building_cost_summary.js"
    ).read_text(encoding="utf-8")
    assert "visibilitychange" not in (
        ROOT / "static" / "dashboard_pinned.js"
    ).read_text(encoding="utf-8")
    assert "visibilitychange" not in (
        ROOT / "static" / "owner_scope.js"
    ).read_text(encoding="utf-8")
