import copy
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from app import InMemoryFirestoreStore, app


DATABASE_URL = (
    "https://mannat-moon-construction-default-rtdb."
    "asia-southeast1.firebasedatabase.app"
)


class FakePresenceReference:
    def __init__(
        self,
        records,
        key=None,
        query_child=None,
        query_start=None,
        query_end=None,
        metrics=None,
    ):
        self.records = records
        self.key = key
        self.query_child = query_child
        self.query_start = query_start
        self.query_end = query_end
        self.metrics = metrics if metrics is not None else {}

    def child(self, key):
        return FakePresenceReference(
            self.records, key=key, metrics=self.metrics
        )

    def order_by_child(self, child):
        return FakePresenceReference(
            self.records,
            query_child=child,
            query_start=self.query_start,
            query_end=self.query_end,
            metrics=self.metrics,
        )

    def start_at(self, value):
        return FakePresenceReference(
            self.records,
            query_child=self.query_child,
            query_start=value,
            query_end=self.query_end,
            metrics=self.metrics,
        )

    def end_at(self, value):
        return FakePresenceReference(
            self.records,
            query_child=self.query_child,
            query_start=self.query_start,
            query_end=value,
            metrics=self.metrics,
        )

    def get(self):
        if self.key is not None:
            return copy.deepcopy(self.records.get(self.key))
        if self.query_child is None:
            self.metrics["root_gets"] = self.metrics.get("root_gets", 0) + 1
            return copy.deepcopy(self.records)
        self.metrics["indexed_queries"] = self.metrics.get("indexed_queries", 0) + 1
        self.metrics["last_query"] = (
            self.query_child,
            self.query_start,
            self.query_end,
        )
        return copy.deepcopy({
            key: record
            for key, record in self.records.items()
            if isinstance(record, dict)
            and isinstance(record.get(self.query_child), (int, float))
            and (
                self.query_start is None
                or record[self.query_child] >= self.query_start
            )
            and (
                self.query_end is None
                or record[self.query_child] <= self.query_end
            )
        })

    def set(self, value):
        error = self.metrics.pop("set_error", None)
        if error is not None:
            raise error
        self.records[self.key] = copy.deepcopy(value)

    def delete(self):
        self.records.pop(self.key, None)

    def transaction(self, update):
        hook = self.metrics.pop("transaction_hook", None)
        if hook:
            hook(self.key, self.records)
        updated = update(copy.deepcopy(self.records.get(self.key)))
        if updated is None:
            self.records.pop(self.key, None)
        else:
            self.records[self.key] = copy.deepcopy(updated)
        return copy.deepcopy(updated)


class FakeRateLimitSnapshot:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        self.exists = value is not None

    def to_dict(self):
        return copy.deepcopy(self.value)


class FakeRateLimitDocument:
    def __init__(self, database, collection, document_id):
        self.database = database
        self.collection = collection
        self.document_id = document_id

    @property
    def key(self):
        return self.collection, self.document_id


class FakeRateLimitTransaction:
    def __init__(self, database):
        self.database = database
        self.writes = []

    def get(self, reference):
        return iter([FakeRateLimitSnapshot(self.database.documents.get(reference.key))])

    def set(self, reference, value):
        self.writes.append((reference.key, copy.deepcopy(value)))

    def delete(self, reference):
        self.writes.append((reference.key, None))


class FakeSharedRateLimitDatabase:
    def __init__(self):
        self.documents = {}
        self.lock = app_module.threading.RLock()

    def collection(self, name):
        return self

    def document(self, document_id):
        return FakeRateLimitDocument(self, "presence_rate_limits", document_id)

    def transaction(self):
        return FakeRateLimitTransaction(self)


@pytest.fixture
def presence_environment(monkeypatch):
    store = InMemoryFirestoreStore()
    store.save_user({
        "id": "staff-user",
        "username": "staff-login",
        "full_name": "Trusted Staff Name",
        "email": "staff@example.invalid",
        "role": "office_staff",
        "custom_staff_type": "",
        "password_hash": generate_password_hash("CurrentSecure1!"),
        "must_change_password": False,
        "status": "active",
        "is_active": True,
        "active_sessions": ["staff-session", "second-session"],
        "permissions": [],
    })
    records = {}
    firebase_app = SimpleNamespace(options={"databaseURL": DATABASE_URL})
    store.firebase_app = firebase_app
    reference = FakePresenceReference(records)

    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "online-presence-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    monkeypatch.setattr(app_module, "_last_presence_cleanup_ms", 0)
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "mannat-moon-construction")
    monkeypatch.setenv("FIREBASE_DATABASE_URL", DATABASE_URL)
    monkeypatch.setattr(
        app_module,
        "firebase_database",
        SimpleNamespace(reference=lambda path, app: reference),
    )

    return store, records, reference


def _client_for_session(user_id="staff-user", session_id="staff-session"):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id
        flask_session["role"] = "forged-role-is-not-authoritative"
        flask_session["session_id"] = session_id
    return client


def _csrf_token(client):
    response = client.get("/profile-settings")
    assert response.status_code == 200
    with client.session_transaction() as flask_session:
        return flask_session["authenticated_form_csrf"]


def _heartbeat(client, csrf_token, *, origin="http://localhost", payload=None):
    return client.post(
        "/api/presence/heartbeat",
        json=payload or {},
        headers={"X-CSRF-Token": csrf_token, "Origin": origin},
    )


def test_presence_endpoints_require_a_valid_authenticated_session(presence_environment):
    client = app.test_client()

    assert client.post("/api/presence/heartbeat").status_code == 401
    assert client.get("/api/presence/roster").status_code == 401


def test_active_staff_can_create_presence_and_read_trusted_roster(presence_environment):
    _store, records, _reference = presence_environment
    client = _client_for_session()
    response = _heartbeat(
        client,
        _csrf_token(client),
        payload={
            "uid": "forged-user",
            "username": "forged",
            "role": "super_admin",
            "token": "untrusted-token",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"success": True, "lease_seconds": 90}
    assert len(records) == 1
    record_key, record = next(iter(records.items()))
    assert len(record_key) == 64
    assert record["user_id"] == "staff-user"
    assert record["expires_at"] - record["last_seen_at"] == 90_000
    assert "session_id" not in record
    assert "display_name" not in record
    assert "staff-session" not in record.values()
    assert "untrusted-token" not in repr(record)
    assert "untrusted-token" not in response.get_data(as_text=True)

    roster_response = client.get("/api/presence/roster")
    assert roster_response.status_code == 200
    assert roster_response.get_json() == {
        "staff": [{"display_name": "Trusted Staff Name", "status": "online"}]
    }
    assert "staff-user" not in roster_response.get_data(as_text=True)
    assert "staff-session" not in roster_response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        ("inactive", 401),
        ("revoked", 401),
        ("superseded", 401),
        ("forced_password_change", 403),
    ],
)
def test_presence_heartbeat_and_roster_reject_ineligible_accounts(
    presence_environment, mutation, expected_status
):
    store, records, _reference = presence_environment
    client = _client_for_session()
    csrf_token = _csrf_token(client)
    user = store.get_user("staff-user")
    if mutation == "inactive":
        user["is_active"] = False
        user["status"] = "inactive"
    elif mutation == "revoked":
        user["active_sessions"] = []
    elif mutation == "superseded":
        user["active_sessions"] = ["replacement-session"]
    else:
        user["must_change_password"] = True
    store.save_user(user)

    assert _heartbeat(client, csrf_token).status_code == expected_status
    assert client.get("/api/presence/roster").status_code == expected_status
    assert records == {}


@pytest.mark.parametrize(
    ("csrf_token", "origin"),
    [
        ("", "http://localhost"),
        ("invalid", "http://localhost"),
        ("valid", "https://attacker.example"),
    ],
)
def test_presence_heartbeat_requires_csrf_and_same_origin(
    presence_environment, csrf_token, origin
):
    _store, records, _reference = presence_environment
    client = _client_for_session()
    token = _csrf_token(client) if csrf_token == "valid" else csrf_token

    response = _heartbeat(client, token, origin=origin)

    assert response.status_code == 400
    assert records == {}


def test_heartbeat_is_idempotent_rate_limited_and_renews_lease(
    presence_environment, monkeypatch
):
    _store, records, _reference = presence_environment
    client = _client_for_session()
    csrf_token = _csrf_token(client)
    now = [app_module.time.time()]
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    first = _heartbeat(client, csrf_token)
    now[0] += 24
    duplicate = _heartbeat(client, csrf_token)

    assert first.status_code == 200
    assert first.headers["X-Correlation-ID"]
    assert duplicate.status_code == 429
    assert duplicate.headers["Retry-After"] == "1"
    assert len(records) == 1
    assert duplicate.headers["X-Correlation-ID"] != first.headers["X-Correlation-ID"]

    now[0] += 36
    next_minute = _heartbeat(client, csrf_token)
    assert next_minute.status_code == 200
    assert records[next(iter(records))]["last_seen_at"] == int(now[0] * 1000)

    assert client.get("/api/presence/roster").get_json()["staff"] == [
        {"display_name": "Trusted Staff Name", "status": "online"}
    ]


def test_roster_requests_are_shared_rate_limited(presence_environment):
    client = _client_for_session()
    _csrf_token(client)

    first = client.get("/api/presence/roster")
    duplicate = client.get("/api/presence/roster")

    assert first.status_code == 200
    assert duplicate.status_code == 429
    assert duplicate.headers["Retry-After"] == str(
        app_module.PRESENCE_ROSTER_MIN_INTERVAL_SECONDS
    )


def test_shared_rate_limit_store_failure_fails_closed(presence_environment, monkeypatch):
    store, records, _reference = presence_environment
    client = _client_for_session()
    csrf_token = _csrf_token(client)

    def unavailable_rate_limit_store(*_args, **_kwargs):
        raise RuntimeError("shared rate-limit store unavailable")

    monkeypatch.setattr(store, "claim_presence_request", unavailable_rate_limit_store)

    response = _heartbeat(client, csrf_token)

    assert response.status_code == 503
    assert records == {}


def test_failed_rtdb_write_releases_only_its_rate_limit_claim(
    presence_environment,
):
    store, records, reference = presence_environment
    client = _client_for_session()
    csrf_token = _csrf_token(client)
    reference.metrics["set_error"] = RuntimeError("RTDB write failed")

    failed = _heartbeat(client, csrf_token)
    retried = _heartbeat(client, csrf_token)

    assert failed.status_code == 503
    assert retried.status_code == 200
    assert len(records) == 1
    assert next(iter(store.presence_rate_limits.values()))["sessions"]


def test_firestore_rate_limit_transaction_is_shared_between_store_instances(
    monkeypatch,
):
    database = FakeSharedRateLimitDatabase()

    def transactional(callback):
        def run(transaction):
            with transaction.database.lock:
                result = callback(transaction)
                for key, value in transaction.writes:
                    transaction.database.documents[key] = value
                return result

        return run

    monkeypatch.setattr(
        app_module,
        "firestore",
        SimpleNamespace(transactional=transactional),
    )
    first_worker_store = object.__new__(app_module.FirebaseFirestoreStore)
    second_worker_store = object.__new__(app_module.FirebaseFirestoreStore)
    first_worker_store.db = database
    second_worker_store.db = database

    assert first_worker_store.claim_presence_request(
        "trusted-user-key", "trusted-session-key", "heartbeat", 100_000, 25_000
    ) == (True, 0)
    assert second_worker_store.claim_presence_request(
        "trusted-user-key", "trusted-session-key", "heartbeat", 100_001, 25_000
    ) == (False, 25)
    assert len(database.documents) == 1
    assert second_worker_store.claim_presence_request(
        "trusted-user-key", "trusted-session-key", "heartbeat", 125_000, 25_000
    ) == (True, 0)


def test_firestore_rate_limit_release_is_atomic_and_matches_claim_timestamp(
    monkeypatch,
):
    database = FakeSharedRateLimitDatabase()

    def transactional(callback):
        def run(transaction):
            with transaction.database.lock:
                result = callback(transaction)
                for key, value in transaction.writes:
                    if value is None:
                        transaction.database.documents.pop(key, None)
                    else:
                        transaction.database.documents[key] = value
                return result

        return run

    monkeypatch.setattr(
        app_module,
        "firestore",
        SimpleNamespace(transactional=transactional),
    )
    store = object.__new__(app_module.FirebaseFirestoreStore)
    store.db = database

    assert store.claim_presence_request(
        "trusted-user-key", "trusted-session-key", "heartbeat", 100_000, 25_000
    ) == (True, 0)
    assert store.release_presence_request(
        "trusted-user-key", "trusted-session-key", "heartbeat", 99_999
    ) is False
    assert store.release_presence_request(
        "trusted-user-key", "trusted-session-key", "heartbeat", 100_000
    ) is True
    assert database.documents == {}


def test_presence_stages_are_logged_with_correlation_and_without_identity(
    presence_environment, caplog
):
    caplog.set_level("INFO", logger=app_module.app.logger.name)
    _store, _records, _reference = presence_environment
    client = _client_for_session()
    csrf_token = _csrf_token(client)

    heartbeat = _heartbeat(client, csrf_token)
    roster = client.get("/api/presence/roster")

    assert heartbeat.status_code == 200
    assert roster.status_code == 200
    for response in (heartbeat, roster):
        correlation_id = response.headers["X-Correlation-ID"]
        correlated_records = [
            record for record in caplog.records
            if correlation_id in record.getMessage()
        ]
        assert correlated_records
        assert any("elapsed_ms=" in record.getMessage() for record in correlated_records)
        assert any("outcome=completed" in record.getMessage() for record in correlated_records)
        messages = "\n".join(record.getMessage() for record in correlated_records)
        assert "staff-user" not in messages
        assert "staff-session" not in messages
        assert "staff@example.invalid" not in messages
        assert "test-csrf-token" not in messages
    heartbeat_messages = "\n".join(
        record.getMessage() for record in caplog.records
        if heartbeat.headers["X-Correlation-ID"] in record.getMessage()
    )
    roster_messages = "\n".join(
        record.getMessage() for record in caplog.records
        if roster.headers["X-Correlation-ID"] in record.getMessage()
    )
    assert "stage=heartbeat_rate_limit_transaction" in heartbeat_messages
    assert "stage=rtdb_heartbeat_write" in heartbeat_messages
    assert "stage=roster_rate_limit_transaction" in roster_messages
    assert "stage=rtdb_live_presence_query" in roster_messages
    assert "stage=firestore_user_lookup" in roster_messages


def test_presence_stage_failure_logs_redacted_message_and_stack(caplog):
    caplog.set_level("WARNING", logger=app_module.app.logger.name)

    class InvalidArgumentError(Exception):
        pass

    with pytest.raises(InvalidArgumentError):
        app_module._presence_run_stage(
            "test-correlation",
            "rtdb_live_presence_query",
            lambda: (_ for _ in ()).throw(
                InvalidArgumentError(
                    'Invalid query; token="private-token" cookie=session-secret '
                    'authorization=Bearer private-bearer '
                    'url=https://example.invalid/db?auth=private-query-token'
                )
            ),
        )

    message = next(
        record.getMessage()
        for record in caplog.records
        if "stage=rtdb_live_presence_query outcome=error" in record.getMessage()
    )
    assert "InvalidArgumentError" in message
    assert "Invalid query" in message
    assert "[REDACTED]" in message
    assert "private-token" not in message
    assert "session-secret" not in message
    assert "private-bearer" not in message
    assert "private-query-token" not in message
    assert "stack=" in message


def test_in_memory_rate_limiter_fails_closed_in_production(
    presence_environment, monkeypatch
):
    store, _records, _reference = presence_environment
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)

    with pytest.raises(RuntimeError, match="shared Firestore rate-limit store"):
        store.claim_presence_request("user", "session", "heartbeat", 100_000, 25_000)


def test_expired_lease_is_not_returned_in_roster(presence_environment, monkeypatch):
    _store, records, _reference = presence_environment
    client = _client_for_session()
    csrf_token = _csrf_token(client)
    now = [app_module.time.time()]
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    assert _heartbeat(client, csrf_token).status_code == 200
    now[0] += 91

    response = client.get("/api/presence/roster")

    assert response.status_code == 200
    assert response.get_json() == {"staff": []}
    assert len(records) == 1


def test_expired_record_cleanup_is_throttled_across_heartbeats(
    presence_environment, monkeypatch
):
    _store, records, reference = presence_environment
    client = _client_for_session()
    csrf_token = _csrf_token(client)
    now = [app_module.time.time()]
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    assert _heartbeat(client, csrf_token).status_code == 200
    assert reference.metrics["indexed_queries"] == 1
    records["expired-session"] = {
        "user_id": "inactive-user",
        "last_seen_at": int(now[0] * 1000) - 100_000,
        "expires_at": int(now[0] * 1000) - 1,
    }

    now[0] += 60
    assert _heartbeat(client, csrf_token).status_code == 200
    assert "expired-session" in records
    assert reference.metrics["indexed_queries"] == 1

    now[0] += app_module.PRESENCE_EXPIRED_CLEANUP_MIN_INTERVAL_SECONDS
    assert _heartbeat(client, csrf_token).status_code == 200
    assert "expired-session" not in records
    assert reference.metrics["indexed_queries"] == 2


def test_expiry_cleanup_preserves_a_lease_renewed_after_the_expiry_query(
    presence_environment,
):
    _store, records, reference = presence_environment
    now_ms = 1_000_000
    record_key = "a" * 64
    records[record_key] = {
        "user_id": "staff-user",
        "last_seen_at": now_ms - 91_000,
        "expires_at": now_ms - 1,
    }
    renewed = {
        "user_id": "staff-user",
        "last_seen_at": now_ms,
        "expires_at": now_ms + 90_000,
    }

    reference.metrics["transaction_hook"] = (
        lambda key, current_records: current_records.update({key: renewed})
    )
    app_module._prune_expired_presence_records(reference, now_ms)

    assert records[record_key] == renewed


def test_roster_queries_only_live_leases_and_returns_minimal_deduplicated_data(
    presence_environment, monkeypatch
):
    store, records, reference = presence_environment
    client = _client_for_session()
    _csrf_token(client)
    user = store.get_user("staff-user")
    user["full_name"] = "A" * 200
    store.save_user(user)
    now_ms = int(app_module.time.time() * 1000)
    monkeypatch.setattr(app_module.time, "time", lambda: now_ms / 1000)
    first_key = app_module._presence_record_key("staff-user", "staff-session")
    second_key = app_module._presence_record_key("staff-user", "second-session")
    records.update({
        first_key: {
            "user_id": "staff-user",
            "last_seen_at": now_ms,
            "expires_at": now_ms + 10_000,
            "session_id": "must-not-be-returned",
            "token": "must-not-be-returned",
        },
        second_key: {
            "user_id": "staff-user",
            "last_seen_at": now_ms,
            "expires_at": now_ms + 20_000,
        },
        "expired": {
            "user_id": "inactive-user",
            "last_seen_at": now_ms - 100_000,
            "expires_at": now_ms,
        },
    })
    monkeypatch.setattr(
        store,
        "list_users",
        lambda: pytest.fail("roster must not enumerate every user"),
    )

    response = client.get("/api/presence/roster")

    assert response.status_code == 200
    assert response.get_json() == {
        "staff": [{"display_name": "A" * 120, "status": "online"}]
    }
    assert reference.metrics.get("root_gets", 0) == 0
    assert reference.metrics["indexed_queries"] == 1
    assert reference.metrics["last_query"] == ("expires_at", now_ms + 1, None)
    assert "must-not-be-returned" not in response.get_data(as_text=True)
    assert "staff-user" not in response.get_data(as_text=True)


def test_multiple_sessions_deduplicate_roster_and_logout_removes_only_one(
    presence_environment,
):
    store, records, _reference = presence_environment
    first_client = _client_for_session(session_id="staff-session")
    second_client = _client_for_session(session_id="second-session")
    assert _heartbeat(first_client, _csrf_token(first_client)).status_code == 200
    assert _heartbeat(second_client, _csrf_token(second_client)).status_code == 200
    assert len(records) == 2

    roster = first_client.get("/api/presence/roster").get_json()
    assert roster["staff"] == [{"display_name": "Trusted Staff Name", "status": "online"}]

    with first_client.session_transaction() as flask_session:
        csrf_token = flask_session["authenticated_form_csrf"]
    logout_response = first_client.post("/logout", data={"csrf_token": csrf_token})

    assert logout_response.status_code == 302
    assert len(records) == 1
    assert store.get_user("staff-user")["active_sessions"] == ["second-session"]
    assert second_client.get("/api/presence/roster").get_json()["staff"] == [
        {"display_name": "Trusted Staff Name", "status": "online"}
    ]
    assert first_client.post("/logout").status_code == 302
    assert len(records) == 1
    assert store.get_user("staff-user")["active_sessions"] == ["second-session"]


def test_logout_keeps_authentication_cleanup_when_rtdb_is_unavailable(
    presence_environment, monkeypatch
):
    store, records, _reference = presence_environment
    client = _client_for_session()
    csrf_token = _csrf_token(client)
    assert _heartbeat(client, csrf_token).status_code == 200

    def unavailable_reference(path, app):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        app_module,
        "firebase_database",
        SimpleNamespace(reference=unavailable_reference),
    )
    response = client.post("/logout", data={"csrf_token": csrf_token})

    assert response.status_code == 302
    assert store.get_user("staff-user")["active_sessions"] == ["second-session"]
    assert len(records) == 1


def test_authenticated_navigation_renders_presence_widget(presence_environment):
    client = _client_for_session()

    response = client.get("/")

    assert response.status_code == 200
    assert b"data-presence-widget" in response.data
    assert b"online_presence.js" in response.data


@pytest.mark.parametrize("failure", ["database_unavailable", "missing_url", "wrong_project_url"])
def test_presence_fails_closed_when_database_is_unavailable_or_misconfigured(
    presence_environment, monkeypatch, failure
):
    _store, _records, reference = presence_environment
    client = _client_for_session()
    csrf_token = _csrf_token(client)
    if failure == "database_unavailable":
        monkeypatch.setattr(
            app_module,
            "firebase_database",
            SimpleNamespace(
                reference=lambda path, app: (_ for _ in ()).throw(RuntimeError("offline"))
            ),
        )
    elif failure == "missing_url":
        monkeypatch.delenv("FIREBASE_DATABASE_URL")
    else:
        monkeypatch.setenv(
            "FIREBASE_DATABASE_URL",
            "https://different-project-default-rtdb.asia-southeast1.firebasedatabase.app",
        )

    response = _heartbeat(client, csrf_token)

    assert response.status_code == 503
    if failure == "missing_url":
        assert "FIREBASE_DATABASE_URL" in response.get_json()["error"]
    if failure == "wrong_project_url":
        assert "belonging to FIREBASE_PROJECT_ID" in response.get_json()["error"]
    assert reference.records == {}


def test_roster_database_failure_returns_no_store_service_error(
    presence_environment, monkeypatch
):
    _store, _records, _reference = presence_environment
    client = _client_for_session()
    _csrf_token(client)

    class UnavailableReference(FakePresenceReference):
        def order_by_child(self, _child):
            return self

        def start_at(self, _value):
            return self

        def get(self):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        app_module,
        "firebase_database",
        SimpleNamespace(reference=lambda path, app: UnavailableReference({})),
    )
    response = client.get("/api/presence/roster")

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {
        "error": "Online presence is temporarily unavailable. Please try again shortly."
    }


def test_database_url_validation_rejects_other_projects_and_insecure_schemes():
    assert app_module._get_firebase_database_url("mannat-moon-construction") == DATABASE_URL
    for url in (
        "http://mannat-moon-construction-default-rtdb.asia-southeast1.firebasedatabase.app",
        "https://different-project-default-rtdb.asia-southeast1.firebasedatabase.app",
        f"{DATABASE_URL}/unexpected/path",
        f"{DATABASE_URL}?redirect=elsewhere",
    ):
        with pytest.raises(RuntimeError, match="FIREBASE_DATABASE_URL"):
            app_module._validate_firebase_database_url(url, "mannat-moon-construction")


def test_firebase_admin_initialization_receives_the_validated_database_url(
    presence_environment, monkeypatch
):
    _store, _records, _reference = presence_environment
    captured = {}
    firebase_app = SimpleNamespace(
        project_id="mannat-moon-construction",
        credential=object(),
        options={"databaseURL": DATABASE_URL},
    )

    def get_uninitialized_default_app():
        raise ValueError("No default app is initialized.")

    monkeypatch.setattr(
        app_module,
        "firebase_admin",
        SimpleNamespace(
            get_app=get_uninitialized_default_app,
            initialize_app=lambda credential, options, **kwargs: (
                captured.update(options=options) or firebase_app
            ),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "credentials",
        SimpleNamespace(ApplicationDefault=lambda: object()),
    )
    monkeypatch.setattr(
        app_module,
        "firestore",
        SimpleNamespace(client=lambda app: object()),
    )
    monkeypatch.delenv("FIRESTORE_GRPC_AUTH_TRANSPORT", raising=False)
    store = object.__new__(app_module.FirebaseFirestoreStore)
    store.project_id = "mannat-moon-construction"
    store.service_account_path = None

    initialized_app, _database = store._initialize_client()

    assert initialized_app is firebase_app
    assert captured["options"] == {
        "projectId": "mannat-moon-construction",
        "databaseURL": DATABASE_URL,
    }
