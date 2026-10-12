import pytest

import app as app_module


class FakeUserStore:
    def get_user(self, user_id):
        return {
            "id": user_id,
            "role": "office_staff",
            "status": "active",
            "is_active": True,
            "active_sessions": ["active-session"],
        }


class FakeRecord:
    def __init__(self, date):
        self.date = date

    def to_dict(self):
        return {"date": self.date}


class FakeRecordQuery:
    def __init__(self, records):
        self.records = records
        self.filters = []

    def where(self, *, filter):
        self.filters.append((filter.field_path, filter.op_string, filter.value))
        return self

    def stream(self):
        return self.records


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    monkeypatch.setitem(app_module.app.config, "FIRESTORE_STORE", FakeUserStore())
    return app_module.app.test_client()


def test_calendar_dates_returns_unique_in_range_report_dates(client, monkeypatch):
    query = FakeRecordQuery([
        FakeRecord("2026-09-03"),
        FakeRecord("2026-09-03"),
        FakeRecord("2026-09-19"),
        FakeRecord("2026-10-01"),
    ])
    monkeypatch.setattr(app_module, "get_daily_records_collection", lambda: query)

    with client.session_transaction() as session:
        session["user_id"] = "user-1"
        session["session_id"] = "active-session"
        session["daily_reports_csrf"] = "daily-reports-test-csrf"

    response = client.get("/api/daily-records/dates?start=2026-09-01&end=2026-09-30")

    assert response.status_code == 200
    assert response.get_json() == {"dates": ["2026-09-03", "2026-09-19"]}
    assert query.filters == [
        ("date", ">=", "2026-09-01"),
        ("date", "<=", "2026-09-30"),
    ]


def test_calendar_dates_requires_authentication(client, monkeypatch):
    monkeypatch.setattr(app_module, "get_daily_records_collection", lambda: pytest.fail("Firestore must not be read"))

    response = client.get("/api/daily-records/dates?start=2026-09-01&end=2026-09-30")

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-02-30", "2026-03-01"), ("2026-09-10", "2026-09-01"), ("2026-9-01", "2026-09-30")],
)
def test_calendar_dates_rejects_invalid_ranges(client, monkeypatch, start, end):
    monkeypatch.setattr(app_module, "get_daily_records_collection", lambda: pytest.fail("Firestore must not be read"))
    with client.session_transaction() as session:
        session["user_id"] = "user-1"
        session["session_id"] = "active-session"
        session["daily_reports_csrf"] = "daily-reports-test-csrf"

    response = client.get(f"/api/daily-records/dates?start={start}&end={end}")

    assert response.status_code == 400