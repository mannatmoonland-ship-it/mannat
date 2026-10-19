import io
import json
from pathlib import Path

import pytest

from app import app
from snapshot_service import create_snapshot


ROOT = Path(__file__).parents[1]


class FakeStore:
    def __init__(self, role="super_admin"):
        self.user = {
            "id": "user-1",
            "username": "snapshot-admin",
            "full_name": "Snapshot Admin",
            "role": role,
            "active_sessions": ["session-1"],
        }

    def get_user(self, user_id):
        return self.user if user_id == self.user["id"] else None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "snapshot-test-secret")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", FakeStore())
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "mannat-moon-construction")
    monkeypatch.setenv(
        "FIREBASE_WEB_CONFIG",
        json.dumps({
            "apiKey": "test-public-key",
            "authDomain": "mannat-moon-construction.firebaseapp.com",
            "projectId": "mannat-moon-construction",
            "appId": "test-app-id",
        }),
    )
    return app.test_client()


def login(client, role="super_admin"):
    app.config["FIRESTORE_STORE"] = FakeStore(role)
    with client.session_transaction() as session:
        session["user_id"] = "user-1"
        session["session_id"] = "session-1"


def test_snapshot_api_requires_login(client):
    response = client.get("/api/snapshots")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_snapshot_operations_are_super_admin_only(client):
    login(client, "office_staff")

    response = client.get("/api/snapshots")

    assert response.status_code == 403


def test_snapshot_page_and_dashboard_card_are_admin_only(client):
    login(client)
    snapshot_page = client.get("/snapshots")
    dashboard = client.get("/")
    assert snapshot_page.status_code == 200
    assert b"<h1>Snapshot</h1>" in snapshot_page.data
    assert b'<a class="mm-app-brand" href="/" aria-label="Mannat Moon Construction Documentation home">' in snapshot_page.data
    assert b'<a class="mm-nav-link"\n               href="/"' in snapshot_page.data
    assert b'<span>Dashboard</span>' in snapshot_page.data
    assert b"Snapshot Admin" in dashboard.data
    assert b"Super Admin" in dashboard.data
    for card_text in (b"Trade Builder", b"User Management", b"Snapshot", b"OPEN TRADE BUILDER", b"MANAGE USERS"):
        assert card_text in dashboard.data
    for other_card in (b'href="/labour-payments"', b'href="/daily-reports"', b'href="/ledger-prints"', b'href="/drawings"'):
        assert other_card in dashboard.data

    for role in ("office_staff", "supervisor"):
        login(client, role)
        dashboard = client.get("/")
        role_label = "Office Staff" if role == "office_staff" else "Supervisor"
        assert b"Snapshot Admin" in dashboard.data
        assert role_label.encode() in dashboard.data
        for restricted_card in (b'href="/trade-builder"', b'href="/user-management"', b'href="/snapshots"'):
            assert restricted_card not in dashboard.data
        for other_card in (b'href="/labour-payments"', b'href="/daily-reports"', b'href="/ledger-prints"', b'href="/drawings"'):
            assert other_card in dashboard.data


def test_snapshot_history_offers_download_only_for_verified_snapshots():
    javascript = (ROOT / "static" / "snapshots.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "static" / "snapshots.css").read_text(encoding="utf-8")

    assert 'class="snapshot-icon-button"' in javascript
    assert 'aria-label="${label}" title="${label}"' in javascript
    assert 'aria-label="Download verified snapshot" title="Download verified snapshot"' in javascript
    assert 'snapshot.verified ? actionButton("restore", snapshot.filename, "Restore") + downloadLink(snapshot.filename) : ""' in javascript
    assert ".snapshot-icon-button:hover" in stylesheet


def test_dashboard_displays_custom_staff_type_when_role_is_custom(client):
    login(client, "Site engineer")
    app.config["FIRESTORE_STORE"].user["custom_staff_type"] = "Site Engineer"

    dashboard = client.get("/")

    assert b"Snapshot Admin" in dashboard.data
    assert b"Site Engineer" in dashboard.data


def test_signed_in_non_admin_can_fetch_public_web_config_without_private_key(client):
    login(client, "office_staff")

    response = client.get("/api/firebase-web-config")

    assert response.status_code == 200
    assert response.get_json()["projectId"] == "mannat-moon-construction"
    assert "private_key" not in response.get_json()


def test_snapshot_write_api_requires_csrf(client):
    login(client)

    response = client.post("/api/snapshots", json={})

    assert response.status_code == 400


def test_verified_snapshot_can_be_downloaded(client, monkeypatch, tmp_path):
    import snapshot_routes

    class EmptyDatabase:
        def collections(self):
            return []

    snapshot = create_snapshot(EmptyDatabase(), "source-project", tmp_path)
    monkeypatch.setattr(snapshot_routes, "_backup_directory", lambda: tmp_path)
    login(client)

    response = client.get(f"/api/snapshots/{snapshot['filename']}/download")

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    assert response.headers["Content-Disposition"].endswith(
        f'filename={snapshot["filename"]}'
    )
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.data == (tmp_path / snapshot["filename"]).read_bytes()


def test_unverified_snapshot_cannot_be_downloaded(client, monkeypatch, tmp_path):
    import snapshot_routes

    filename = "MannatMoon_Snapshot_2026-10-15_120000.zip"
    (tmp_path / filename).write_bytes(b"not a verified ZIP")
    monkeypatch.setattr(snapshot_routes, "_backup_directory", lambda: tmp_path)
    login(client)

    response = client.get(f"/api/snapshots/{filename}/download")

    assert response.status_code == 422
    assert response.get_json()["error"] == (
        "This snapshot failed verification and cannot be downloaded."
    )


def test_snapshot_download_remains_super_admin_only(client):
    login(client, "office_staff")

    response = client.get(
        "/api/snapshots/MannatMoon_Snapshot_2026-10-15_120000.zip/download"
    )

    assert response.status_code == 403


def test_restore_preview_rejects_invalid_service_account_before_firestore_access(client, monkeypatch, tmp_path):
    import snapshot_routes

    class EmptyDatabase:
        def collections(self):
            return []

    create_snapshot(EmptyDatabase(), "source-project", tmp_path)
    monkeypatch.setattr(snapshot_routes, "_backup_directory", lambda: tmp_path)
    login(client)
    with client.session_transaction() as session:
        session["snapshot_csrf_token"] = "csrf-test-token"

    response = client.post(
        "/api/snapshots/MannatMoon_Snapshot_2026-09-27_150000.zip/restore/preview",
        data={
            "destination_type": "new",
            "destination_project_id": "target-project",
            "web_config": "{}",
            "service_account": (io.BytesIO(b"not-json"), "service-account.json"),
        },
        headers={"X-Snapshot-CSRF": "csrf-test-token"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 404
    snapshot = next(tmp_path.glob("MannatMoon_Snapshot_*.zip"))
    response = client.post(
        f"/api/snapshots/{snapshot.name}/restore/preview",
        data={
            "destination_type": "new",
            "destination_project_id": "target-project",
            "web_config": "{}",
            "service_account": (io.BytesIO(b"not-json"), "service-account.json"),
        },
        headers={"X-Snapshot-CSRF": "csrf-test-token"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "credentials file could not be read" in response.get_json()["error"]


def test_switch_rejects_untrusted_project_path(client):
    login(client)
    with client.session_transaction() as session:
        session["snapshot_csrf_token"] = "csrf-test-token"

    response = client.post(
        "/api/snapshots/switch-project",
        json={"project_id": "../other", "confirmed_project_id": "../other"},
        headers={"X-Snapshot-CSRF": "csrf-test-token"},
    )

    assert response.status_code == 400