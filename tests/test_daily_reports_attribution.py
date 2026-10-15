import pytest

import app as app_module


class FakeUserStore:
    def __init__(self):
        self.db = object()
        self.users = {
            "user-1": {
                "id": "user-1",
                "username": "rahim",
                "full_name": "Rahim",
                "avatarId": "fox",
                "role": "office_staff",
                "status": "active",
                "is_active": True,
                "active_sessions": ["active-session"],
            },
            "user-2": {
                "id": "user-2",
                "username": "samira",
                "full_name": "Samira",
                "avatarId": "panda",
                "role": "office_staff",
                "status": "active",
                "is_active": True,
                "active_sessions": ["active-session"],
            },
        }

    def get_user(self, user_id):
        return self.users.get(user_id)

    def get_user_by_username(self, username):
        return next(
            (user for user in self.users.values() if user["username"].lower() == username.lower()),
            None,
        )

    def list_trades(self):
        return [{
            "id": "trade-1",
            "name": "Masonry",
            "workerCategories": [
                {
                    "id": "worker-1",
                    "name": "Masons",
                    "currentWage": 1500,
                    "wageHistory": [{"dailyWage": 1400}],
                }
            ],
            "contractItems": [{"name": "Hidden contract", "amount": 500000}],
            "paymentTransactions": [{"amount": 25000, "remarks": "Sensitive payment"}],
        }]


class FakeRecordSnapshot:
    def __init__(self, data, record_id=None):
        self.exists = data is not None
        self.data = data
        self.id = record_id

    def to_dict(self):
        return self.data


class FakeRecordReference:
    def __init__(self, records, record_id):
        self.records = records
        self.id = record_id

    def get(self):
        return FakeRecordSnapshot(self.records.get(self.id))

    def set(self, data):
        self.records[self.id] = dict(data)

    def update(self, data):
        self.records[self.id].update(data)


class FakeDailyRecordsCollection:
    def __init__(self):
        self.records = {}
        self.next_id = 0

    def where(self, *, filter):
        return FakeDailyRecordsQuery(self, filter.field_path, filter.value)

    def document(self, record_id=None):
        if record_id is None:
            self.next_id += 1
            record_id = f"record-{self.next_id}"
        return FakeRecordReference(self.records, record_id)


class FakeDailyRecordsQuery:
    def __init__(self, collection, field, value):
        self.collection = collection
        self.field = field
        self.value = value

    def stream(self):
        return [
            FakeRecordSnapshot(data, record_id)
            for record_id, data in self.collection.records.items()
            if data.get(self.field) == self.value
        ]


@pytest.fixture
def daily_reports_client(monkeypatch):
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    store = FakeUserStore()
    records = FakeDailyRecordsCollection()
    monkeypatch.setitem(app_module.app.config, "FIRESTORE_STORE", store)
    monkeypatch.setattr(app_module, "get_daily_records_collection", lambda: records)
    return app_module.app.test_client(), store, records


def set_logged_in_user(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["session_id"] = "active-session"
        session["daily_reports_csrf"] = "daily-reports-test-csrf"


def test_report_creator_and_editor_avatars_resolve_from_current_user_profiles(daily_reports_client):
    client, store, records = daily_reports_client
    payload = {"date": "2026-10-03", "activity": "Foundation work"}

    set_logged_in_user(client, "user-1")
    headers = {"X-CSRF-Token": "daily-reports-test-csrf"}
    create_response = client.post("/api/daily-records", json=payload, headers=headers)
    assert create_response.status_code == 201
    record_id = create_response.get_json()["id"]
    assert records.records[record_id]["createdByUserId"] == "user-1"
    assert "avatarId" not in records.records[record_id]

    set_logged_in_user(client, "user-2")
    update_response = client.patch(
        f"/api/daily-records/{record_id}", json=payload, headers=headers
    )
    assert update_response.status_code == 200
    assert records.records[record_id]["last_edited_by_user_id"] == "user-2"
    assert "avatarId" not in records.records[record_id]

    avatars_response = client.get(
        "/api/daily-records/author-avatars?user_id=user-1&user_id=user-2"
    )
    assert avatars_response.status_code == 200
    assert avatars_response.get_json() == {
        "user_ids": {"user-1": "fox", "user-2": "panda"},
        "usernames": {},
    }

    store.users["user-2"]["avatarId"] = "lion"
    refreshed_avatars_response = client.get(
        "/api/daily-records/author-avatars?user_id=user-1&user_id=user-2"
    )
    assert refreshed_avatars_response.get_json()["user_ids"]["user-2"] == "lion"


def test_author_avatar_lookup_supports_legacy_report_usernames(daily_reports_client):
    client, _, _ = daily_reports_client
    set_logged_in_user(client, "user-1")

    response = client.get("/api/daily-records/author-avatars?username=Samira")

    assert response.status_code == 200
    assert response.get_json() == {
        "user_ids": {},
        "usernames": {"samira": "panda"},
    }


def test_deleted_users_keep_daily_report_creator_and_editor_attribution(
    daily_reports_client,
):
    _, store, records = daily_reports_client
    records.records["legacy-report"] = {
        "date": "2026-10-03",
        "createdByUserId": "user-1",
        "last_edited_by_user_id": "user-2",
    }

    app_module.preserve_daily_report_user_attribution(store.users["user-1"])
    app_module.preserve_daily_report_user_attribution(store.users["user-2"])
    del store.users["user-1"]
    del store.users["user-2"]

    displayed_record = app_module._sanitize_daily_report_for_view(
        records.records["legacy-report"]
    )

    assert records.records["legacy-report"]["createdByName"] == "Rahim"
    assert records.records["legacy-report"]["createdByUsername"] == "rahim"
    assert records.records["legacy-report"]["last_edited_by_name"] == "Samira"
    assert records.records["legacy-report"]["last_edited_by_username"] == "samira"
    assert displayed_record["createdByDisplayName"] == "Rahim"
    assert displayed_record["createdByUsername"] == "rahim"
    assert displayed_record["last_edited_by_name"] == "Samira"
    assert displayed_record["createdByAvatarId"] == app_module.DEFAULT_PROFILE_AVATAR_ID


def test_daily_report_trade_options_exclude_financial_fields(daily_reports_client):
    client, _, _ = daily_reports_client
    set_logged_in_user(client, "user-1")

    response = client.get("/api/daily-records/trades")

    assert response.status_code == 200
    assert response.get_json() == {
        "trades": [{
            "id": "trade-1",
            "name": "Masonry",
            "workerCategories": [{"name": "Masons"}],
        }]
    }
    assert response.headers["Cache-Control"] == "no-store"


def test_author_avatar_lookup_requires_authentication(daily_reports_client):
    client, _, _ = daily_reports_client

    response = client.get("/api/daily-records/author-avatars?user_id=user-1")

    assert response.status_code == 401
