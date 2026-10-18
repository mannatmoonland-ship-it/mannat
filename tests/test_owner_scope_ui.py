from copy import deepcopy
from datetime import datetime, timezone
import uuid

import pytest

from app import app
from owner_scope import (
    LEGACY_OWNER_ACCOUNT_COLLECTION,
    LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    OWNER_SCOPE_CATEGORIES,
    OWNER_SCOPE_COLLECTION,
    OWNER_SCOPE_DOCUMENT_ID,
)


def _legacy_fixture():
    transactions = [
        {
            "id": f"legacy-{index:02d}",
            "date": "2025-01-01",
            "amount": 164148 if index < 34 else 164139,
            "paymentMode": "Bank Transfer",
            "remarks": f"Historical transaction {index}",
        }
        for index in range(35)
    ]
    return {
        "name": "OWNERS ACCOUNT",
        "contractItems": [{"id": "legacy-contract"}],
        "workerCategories": [{"id": "legacy-worker"}],
        "paymentTransactions": transactions,
    }


class FakeSnapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self._data = deepcopy(data)
        self.exists = data is not None

    def to_dict(self):
        return deepcopy(self._data)


class FakeQuery:
    def __init__(self, collection, field=None, expected=None, limit_count=None):
        self.collection_ref = collection
        self.field = field
        self.expected = expected
        self.limit_count = limit_count

    def where(self, field=None, operator=None, expected=None, *, filter=None):
        if filter is not None:
            field = filter.field_path
            operator = filter.op_string
            expected = filter.value
        if operator != "==":
            raise AssertionError(f"Unsupported fake query operator: {operator}")
        return FakeQuery(self.collection_ref, field, expected, self.limit_count)

    def limit(self, count):
        return FakeQuery(self.collection_ref, self.field, self.expected, count)

    def stream(self):
        values = self.collection_ref._snapshots()
        if self.field is not None:
            values = [
                snapshot for snapshot in values
                if (snapshot.to_dict() or {}).get(self.field) == self.expected
            ]
        return values[: self.limit_count] if self.limit_count is not None else values


class FakeDocumentReference:
    def __init__(self, database, collection_path, document_id):
        self.database = database
        self.collection_path = collection_path
        self.id = document_id
        self.key = (collection_path, document_id)

    def get(self, transaction=None):
        if transaction is not None:
            return transaction.get(self)
        self.database.reads.append(self.key)
        return FakeSnapshot(self.id, self.database.documents.get(self.key))

    def create(self, data):
        if self.key in self.database.documents:
            raise AssertionError("The test attempted to create a duplicate document.")
        self.database.documents[self.key] = deepcopy(data)
        self.database.writes.append(self.key)

    def update(self, data):
        if self.key not in self.database.documents:
            raise AssertionError("The test attempted to update a missing document.")
        self.database.documents[self.key].update(deepcopy(data))
        self.database.writes.append(self.key)

    def delete(self):
        self.database.documents.pop(self.key, None)
        self.database.writes.append(self.key)

    def collection(self, collection_name):
        return FakeCollectionReference(
            self.database,
            f"{self.collection_path}/{self.id}/{collection_name}",
        )


class FakeCollectionReference(FakeQuery):
    def __init__(self, database, collection_path):
        self.database = database
        self.collection_path = collection_path
        self.collection_ref = self
        self.field = None
        self.expected = None
        self.limit_count = None

    def document(self, document_id=None):
        if document_id is None:
            document_id = f"generated-{self.database.next_id}"
            self.database.next_id += 1
        return FakeDocumentReference(self.database, self.collection_path, document_id)

    def _snapshots(self):
        return [
            FakeSnapshot(document_id, data)
            for (path, document_id), data in self.database.documents.items()
            if path == self.collection_path
        ]

    def stream(self):
        return self._snapshots()

    def where(self, field=None, operator=None, expected=None, *, filter=None):
        return super().where(
            field=field,
            operator=operator,
            expected=expected,
            filter=filter,
        )


class FakeWriteBatch:
    def __init__(self, database):
        self.database = database
        self.operations = []

    def create(self, reference, data):
        self.operations.append((reference, deepcopy(data)))

    def delete(self, reference):
        self.operations.append(("delete", reference))

    def commit(self):
        for operation in self.operations:
            if operation[0] == "delete":
                reference = operation[1]
                self.database.documents.pop(reference.key, None)
                self.database.writes.append(reference.key)
            else:
                reference, data = operation
                if reference.key in self.database.documents:
                    raise AssertionError("The test attempted to create a duplicate document.")
                self.database.documents[reference.key] = data
                self.database.writes.append(reference.key)


class FakeTransaction:
    def __init__(self, database):
        self.database = database
        self.operations = []
        self.started = False
        self._max_attempts = 1
        self._read_only = False
        self._id = None

    def _clean_up(self):
        self.operations.clear()

    def _begin(self, retry_id=None):
        self.started = True
        self._id = "fake-transaction"

    def get(self, reference_or_query):
        if isinstance(reference_or_query, FakeDocumentReference):
            return reference_or_query.get()
        return iter(reference_or_query.stream())

    def update(self, reference, data):
        self.operations.append(("update", reference, deepcopy(data)))

    def delete(self, reference):
        self.operations.append(("delete", reference, None))

    def _commit(self):
        if any(
            action == "update" and reference.key not in self.database.documents
            for action, reference, _data in self.operations
        ):
            raise AssertionError("The test attempted to update a missing document.")
        for action, reference, data in self.operations:
            if action == "update":
                self.database.documents[reference.key].update(data)
            else:
                self.database.documents.pop(reference.key, None)
            self.database.writes.append(reference.key)

    def _rollback(self):
        self.operations.clear()


class FakeAdminDatabase:
    def __init__(self):
        self.legacy = _legacy_fixture()
        self.documents = {
            (
                LEGACY_OWNER_ACCOUNT_COLLECTION,
                LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
            ): deepcopy(self.legacy),
            (OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID): {
                "name": "Owner Scope",
                "legacySource": {
                    "collection": LEGACY_OWNER_ACCOUNT_COLLECTION,
                    "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
                },
                "createdAt": datetime(2025, 1, 1, tzinfo=timezone.utc),
                "updatedAt": datetime(2025, 1, 1, tzinfo=timezone.utc),
            },
        }
        self.writes = []
        self.reads = []
        self.next_id = 1

    def collection(self, collection_name):
        return FakeCollectionReference(self, collection_name)

    def batch(self):
        return FakeWriteBatch(self)

    def transaction(self):
        return FakeTransaction(self)


class FakeUserStore:
    def __init__(self, role="super_admin"):
        self.db = FakeAdminDatabase()
        self.user = {
            "id": "owner-scope-user",
            "username": "owner-scope-test",
            "role": role,
            "status": "active",
            "is_active": True,
            "active_sessions": ["owner-scope-session"],
            "must_change_password": False,
        }
        self.custom_staff_types = []

    def get_user(self, user_id):
        return dict(self.user) if user_id == self.user["id"] else None

    def save_user(self, user):
        self.user = dict(user)

    def list_custom_staff_types(self):
        return list(self.custom_staff_types)

    def save_custom_staff_type(self, staff_type):
        self.custom_staff_types = [
            existing for existing in self.custom_staff_types
            if existing.get("id") != staff_type["id"]
        ]
        self.custom_staff_types.append(dict(staff_type))


@pytest.fixture
def client(monkeypatch):
    store = FakeUserStore()
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "owner-scope-test")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", store)
    test_client = app.test_client()
    with test_client.session_transaction() as session:
        session["user_id"] = "owner-scope-user"
        session["session_id"] = "owner-scope-session"
        session["owner_scope_csrf"] = "test-csrf-token"
    return test_client, store


def _mutation_headers():
    return {
        "X-CSRF-Token": "test-csrf-token",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def _item_payload(name="Water connection", category="site_expense"):
    return {
        "name": name,
        "category": category,
        "paymentDate": "2026-10-07",
        "amountPaid": "1250",
        "paymentMode": "Bank Transfer",
        "remarks": "Owner-level scope item",
    }


def test_owner_scope_page_is_login_protected_and_has_one_finance_navigation_link(client):
    test_client, _ = client
    assert app.test_client().get("/owner-scope").status_code == 302
    response = test_client.get("/owner-scope")
    assert response.status_code == 200
    assert b"Owner Scope Items" in response.data
    assert b"No Owner Scope items added yet." in response.data
    assert b"Legacy Owner Account History" not in response.data
    assert b"LEGACY / HISTORICAL" not in response.data
    assert b"Budget / Capping" not in response.data
    assert b"Remaining" not in response.data
    assert b'name="budgetAmount"' not in response.data
    assert b"ownerScopePaymentHistory" in response.data
    assert b"ownerScopeItemBudget" not in response.data
    table_header = response.data.split(b"<thead>", 1)[1].split(b"</thead>", 1)[0]
    columns = [b"Item", b"Date", b"Category", b"Paid", b"Remarks", b"Actions"]
    positions = [table_header.index(column) for column in columns]
    assert positions == sorted(positions)
    assert b'name="paymentDate" type="date"' in response.data
    assert b'name="amountPaid" type="number" min="0"' in response.data
    assert b'name="paymentMode"' in response.data
    assert b"Payment Date" in response.data
    assert b"Amount Paid" in response.data
    assert b"Payment Mode" in response.data
    assert b'value="Cash"' in response.data
    assert b'value="Bank Transfer"' in response.data
    assert b'id="ownerScopeHistoryTotal"' in response.data
    assert b'<th scope="col">Remarks</th>' in table_header
    assert response.data.count(b">Owner Scope</a>") == 1
    assert b"owner_scope.js" in response.data
    assert b"dialogs.css" in response.data
    assert b"budgetAmount" not in response.data


def test_owner_scope_read_api_requires_compiled_permission_and_password_change(client):
    test_client, store = client

    store.user["role"] = "Unconfigured Role"
    store.user["custom_staff_type"] = "Unconfigured Role"
    denied = test_client.get("/api/owner-scope")
    assert denied.status_code == 403

    store.user.update({
        "role": "super_admin",
        "custom_staff_type": "",
        "must_change_password": True,
    })
    password_change_required = test_client.get("/api/owner-scope")
    assert password_change_required.status_code == 403

    store.user["must_change_password"] = False
    allowed = test_client.get("/api/owner-scope")
    assert allowed.status_code == 200


def test_add_form_defaults_to_local_today_and_keeps_payment_date_editable():
    from pathlib import Path

    root = Path(__file__).parents[1]
    template = (root / "templates/owner_scope.html").read_text(encoding="utf-8")
    script = (root / "static/owner_scope.js").read_text(encoding="utf-8")
    assert 'from "./secure_request_id.mjs";' in script
    assert "reserveSecureRequestId" in script
    assert "initializeOwnerScopeCreateRequestId();" in script
    assert script.index("initializeOwnerScopeCreateRequestId();") < script.rindex(
        "loadOwnerScope();"
    )
    assert 'page.saveButton.disabled = true;' in script
    assert "createRequestIdError" in script
    assert "cryptoApi.getRandomValues" in (
        root / "static/secure_request_id.mjs"
    ).read_text(encoding="utf-8")

    assert 'type="date" required disabled' in template
    assert "const hasPaymentFields = !item || editingPaymentDetails;" in script
    assert "if (!item) page.paymentDate.value = localDateInputValue();" in script
    assert 'import { formatDateWithDay, localDateInputValue } from "./date_format.js";' in script
    assert "field.disabled = !hasPaymentFields;" in script
    assert "payload.paymentDate = page.paymentDate.value;" in script
    assert "payload.amountPaid = page.amountPaid.value;" in script
    assert "payload.paymentMode = page.paymentMode.value;" in script


def test_owner_scope_page_exposes_pdf_download_button_and_existing_pdf_libraries(client):
    from pathlib import Path

    test_client, _ = client
    response = test_client.get("/owner-scope")
    assert response.status_code == 200
    assert b'id="downloadOwnerScopePdf"' in response.data
    assert b">Download PDF</button>" in response.data
    assert b"jspdf/2.5.1/jspdf.umd.min.js" in response.data
    assert b"jspdf-autotable/3.8.2/jspdf.plugin.autotable.min.js" in response.data
    assert b"owner_scope_pdf.js" in (Path(__file__).parents[1] / "static/owner_scope.js").read_bytes()


def test_owner_scope_table_uses_linked_payment_date_and_only_edit_delete_actions():
    from pathlib import Path

    root = Path(__file__).parents[1]
    script = (root / "static/owner_scope.js").read_text(encoding="utf-8")
    assert "ownerScopeData.payments.find(payment => payment.expenseItemId === item.id)" in script
    assert 'formatDateWithDay(linkedPayment?.date, "—").replace(", ", ",\\n")' in script
    assert '["edit", "Edit", \'<svg viewBox="0 0 24 24" aria-hidden="true">' in script
    assert '["delete", "Delete", \'<svg viewBox="0 0 24 24" aria-hidden="true">' in script
    assert 'button.setAttribute("aria-label", `${label} Owner Scope item`);' in script
    assert "button.title = `${label} Owner Scope item`;" in script
    assert 'button.innerHTML = icon;' in script


def test_delete_confirmation_uses_simple_custom_mannat_moon_modal():
    from pathlib import Path

    root = Path(__file__).parents[1]
    script = (root / "static/owner_scope.js").read_text(encoding="utf-8")
    confirmation = (root / "static/owner_scope_delete_confirmation.mjs").read_text(encoding="utf-8")
    assert 'import { showConfirmDialog } from "./dialogs.js";' in script
    assert "buildOwnerScopeDeleteConfirmation" in script
    assert "window.confirm(" not in script
    assert "confirm(" not in script
    assert "alert(" not in script
    assert 'title: "Mannat Moon"' in confirmation
    assert 'message: "Are you sure you want to delete this expense?"' in confirmation
    assert '`Item: ${item.name || "Unnamed Owner Scope item"}`' in confirmation
    assert "paymentDates.join(\", \") || \"—\"" in confirmation
    assert "formatMoney(linkedPaymentTotal)" in confirmation
    assert 'confirmText: "Delete"' in confirmation
    assert 'cancelText: "Cancel"' in confirmation
    assert "Payment Mode:" not in confirmation
    assert "Category:" not in confirmation
    assert "Payments Linked:" not in confirmation
    assert "Total Paid:" not in confirmation
    assert "linked payment" not in confirmation.lower()
    assert "payment record" not in confirmation.lower()
    assert "cannot be undone" not in confirmation.lower()
    assert "if (!confirmed) return;" in script
    assert script.index("if (!confirmed) return;") < script.index('method: "DELETE"')
    assert "await loadOwnerScope();" in script


def test_delete_cancellation_returns_before_any_request_or_firestore_write():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "static/owner_scope.js").read_text(encoding="utf-8")
    cancel_guard = script.index("if (!confirmed) return;")
    delete_request = script.index(
        'await requestJson(`/api/owner-scope/items/${encodeURIComponent(item.id)}`, { method: "DELETE" });'
    )
    assert cancel_guard < delete_request
    assert script.count('method: "DELETE"') == 1
    assert "showConfirmDialog(buildOwnerScopeDeleteConfirmation({" in script


def test_owner_scope_items_table_constrains_column_widths_for_wrapping():
    from pathlib import Path

    root = Path(__file__).parents[1]
    template = (root / "templates/owner_scope.html").read_text(encoding="utf-8")
    styles = (root / "static/owner_scope.css").read_text(encoding="utf-8")
    script = (root / "static/owner_scope.js").read_text(encoding="utf-8")
    assert 'class="owner-scope-table owner-scope-items-table"' in template
    assert ".owner-scope-items-table {\n    table-layout: fixed;" in styles
    assert 'appendCell(row, item.remarks || "—");' in script
    for column, width in (
        ("item", "23%"),
        ("date", "13%"),
        ("category", "14%"),
        ("paid", "11%"),
        ("remarks", "24%"),
        ("actions", "15%"),
    ):
        assert f".owner-scope-{column}-column {{\n    width: {width};" in styles


def _add_historical_paid_item(
    store, item_id="historical-item", payment_id="legacy-payment-1"
):
    scope = store.db.collection(OWNER_SCOPE_COLLECTION).document(OWNER_SCOPE_DOCUMENT_ID)
    scope.collection("items").document(item_id).create(
        {
            "name": "Historical expense",
            "category": "site_expense",
            "remarks": "Item remarks",
        }
    )
    scope.collection("payments").document(payment_id).create(
        {
            "expenseItemId": item_id,
            "sourceLegacyTransactionId": payment_id,
            "sourceTransactionId": payment_id,
            "date": "2025-01-01",
            "amount": 1250,
            "paymentMode": "Bank Transfer",
            "remarks": "Historical payment",
        }
    )
    return item_id, payment_id


def test_paid_item_edit_updates_existing_payment_amount_and_date_without_duplicate(client):
    test_client, store = client
    item_id, payment_id = _add_historical_paid_item(store)
    legacy_key = (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID)
    legacy_before = deepcopy(store.db.documents[legacy_key])
    payment_path = f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments"
    payment_key = (payment_path, payment_id)
    original_payment = deepcopy(store.db.documents[payment_key])
    store.db.reads.clear()
    store.db.writes.clear()

    response = test_client.put(
        f"/api/owner-scope/items/{item_id}",
        json={
            **_item_payload(name="Updated historical expense"),
            "paymentDate": "2025-02-03",
            "amountPaid": "2500",
            "paymentMode": "Bank Transfer",
        },
        headers=_mutation_headers(),
    )

    assert response.status_code == 200
    assert store.db.documents[payment_key]["amount"] == 2500
    assert store.db.documents[payment_key]["date"] == "2025-02-03"
    assert store.db.documents[payment_key]["paymentMode"] == "Bank Transfer"
    assert store.db.documents[payment_key]["sourceLegacyTransactionId"] == payment_id
    assert store.db.documents[payment_key]["sourceTransactionId"] == payment_id
    assert len([key for key in store.db.documents if key[0] == payment_path]) == 1
    assert store.db.documents[legacy_key] == legacy_before
    assert not any(path == LEGACY_OWNER_ACCOUNT_COLLECTION for path, _ in store.db.reads)
    assert not any(path == LEGACY_OWNER_ACCOUNT_COLLECTION for path, _ in store.db.writes)

    data = test_client.get("/api/owner-scope").get_json()
    assert sum(payment["amount"] for payment in data["payments"]) == 2500
    assert data["payments"][0]["id"] == payment_id
    assert original_payment["sourceLegacyTransactionId"] == store.db.documents[
        payment_key
    ]["sourceLegacyTransactionId"]


def _owner_scope_request(client, method, path, **kwargs):
    headers = kwargs.pop("headers", _mutation_headers())
    return getattr(client, method)(path, headers=headers, **kwargs)


@pytest.mark.parametrize("role", ["office_staff", "supervisor"])
def test_builtin_operational_roles_can_create_and_edit_but_not_delete(client, role):
    test_client, store = client
    created = _owner_scope_request(
        test_client, "post", "/api/owner-scope/items", json=_item_payload()
    )
    assert created.status_code == 201
    item_id = created.get_json()["id"]

    store.user["role"] = role
    edited = _owner_scope_request(
        test_client,
        "put",
        f"/api/owner-scope/items/{item_id}",
        json=_item_payload(name="Edited by operational role"),
    )
    assert edited.status_code == 200
    deleted = _owner_scope_request(
        test_client, "delete", f"/api/owner-scope/items/{item_id}"
    )
    assert deleted.status_code == 403


@pytest.mark.parametrize(
    ("role", "permission", "method"),
    [
        ("Scope Creator", "owner_scope.create", "post"),
        ("Scope Editor", "owner_scope.edit", "put"),
        ("Scope Deleter", "owner_scope.delete", "delete"),
    ],
)
def test_custom_roles_are_denied_by_default_and_allowed_only_for_compiled_permission(
    client, role, permission, method
):
    test_client, store = client
    existing = _owner_scope_request(
        test_client, "post", "/api/owner-scope/items", json=_item_payload()
    )
    item_id = existing.get_json()["id"]
    store.user.update({"role": role, "custom_staff_type": role})
    path = (
        "/api/owner-scope/items"
        if method == "post"
        else f"/api/owner-scope/items/{item_id}"
    )
    payload = _item_payload(name=f"{role} authorized")
    denied = _owner_scope_request(
        test_client, method, path, **({"json": payload} if method != "delete" else {})
    )
    assert denied.status_code == 403

    store.save_custom_staff_type(
        {"id": role, "name": role, "permissions": [permission]}
    )
    allowed = _owner_scope_request(
        test_client, method, path, **({"json": payload} if method != "delete" else {})
    )
    assert allowed.status_code in ({200, 201} if method != "delete" else {200})


def test_owner_scope_mutations_reject_unauthenticated_and_missing_csrf(client):
    test_client, _store = client
    unauthenticated = app.test_client().post(
        "/api/owner-scope/items",
        json=_item_payload(),
        headers=_mutation_headers(),
    )
    assert unauthenticated.status_code == 401

    missing_csrf = test_client.post(
        "/api/owner-scope/items",
        json=_item_payload(),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert missing_csrf.status_code == 403


@pytest.mark.parametrize("request_id", [None, "", "not-a-uuid", "{" + str(uuid.uuid4()) + "}"])
def test_owner_scope_create_rejects_missing_or_invalid_request_id_without_writes(
    client, request_id
):
    test_client, store = client
    headers = {"X-CSRF-Token": "test-csrf-token"}
    if request_id is not None:
        headers["Idempotency-Key"] = request_id

    response = test_client.post(
        "/api/owner-scope/items",
        json=_item_payload(),
        headers=headers,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "A valid Owner Scope request ID is required."}
    assert store.db.writes == []
    assert not any(
        path.endswith("/items") or path.endswith("/payments")
        for path, _document_id in store.db.documents
    )


def test_owner_scope_mutations_reject_inactive_and_forced_password_change_users(client):
    test_client, store = client
    store.user["status"] = "disabled"
    inactive = _owner_scope_request(
        test_client, "post", "/api/owner-scope/items", json=_item_payload()
    )
    assert inactive.status_code == 401

    store.user.update({
        "status": "active",
        "active_sessions": ["owner-scope-session"],
        "must_change_password": True,
    })
    with test_client.session_transaction() as flask_session:
        flask_session["user_id"] = "owner-scope-user"
        flask_session["session_id"] = "owner-scope-session"
        flask_session["owner_scope_csrf"] = "test-csrf-token"
    forced_change = _owner_scope_request(
        test_client, "post", "/api/owner-scope/items", json=_item_payload()
    )
    assert forced_change.status_code == 403


def test_browser_supplied_authorization_and_identity_fields_cannot_elevate_access(client):
    test_client, store = client
    store.user["role"] = "office_staff"
    with test_client.session_transaction() as flask_session:
        flask_session["role"] = "super_admin"
        flask_session["permissions"] = ["owner_scope.delete"]
    created = test_client.post(
        "/api/owner-scope/items",
        json={
            **_item_payload(),
            "role": "super_admin",
            "permissions": ["owner_scope.delete"],
            "userId": "another-user",
        },
        headers=_mutation_headers(),
    )
    assert created.status_code == 400
    assert not any(
        path.endswith("/items") or path.endswith("/payments")
        for path, _document_id in store.db.writes
    )

    item_id, _payment_id = _add_historical_paid_item(store)
    denied = test_client.delete(
        f"/api/owner-scope/items/{item_id}",
        json={
            "role": "super_admin",
            "permissions": ["owner_scope.delete"],
            "userId": "another-user",
        },
        headers=_mutation_headers(),
    )
    assert denied.status_code == 403


def test_owner_scope_create_retry_is_idempotent_and_conflicting_reuse_is_rejected(client):
    test_client, store = client
    request_id = str(uuid.uuid4())
    headers = {
        "X-CSRF-Token": "test-csrf-token",
        "Idempotency-Key": request_id,
    }
    payload = _item_payload()
    first = test_client.post(
        "/api/owner-scope/items", json=payload, headers=headers
    )
    retry = test_client.post(
        "/api/owner-scope/items", json=payload, headers=headers
    )
    conflict = test_client.post(
        "/api/owner-scope/items",
        json=_item_payload(name="Different content"),
        headers=headers,
    )
    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.get_json() == first.get_json()
    assert conflict.status_code == 409
    assert len([
        key for key in store.db.documents
        if key[0] == f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items"
    ]) == 1
    assert len([
        key for key in store.db.documents
        if key[0] == f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments"
    ]) == 1


def test_owner_scope_edits_preserve_stable_ids_created_at_and_unrelated_fields(client):
    test_client, store = client
    created = test_client.post(
        "/api/owner-scope/items",
        json=_item_payload(),
        headers=_mutation_headers(),
    )
    item_id = created.get_json()["id"]
    item_path = f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items"
    payment_path = f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments"
    payment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"owner-scope-payment:{item_id}"))
    item_ref = store.db.collection(OWNER_SCOPE_COLLECTION).document(
        OWNER_SCOPE_DOCUMENT_ID
    ).collection("items").document(item_id)
    payment_ref = store.db.collection(OWNER_SCOPE_COLLECTION).document(
        OWNER_SCOPE_DOCUMENT_ID
    ).collection("payments").document(payment_id)
    item_ref.update({"unrelatedItemField": {"keep": True}})
    payment_ref.update({"unrelatedPaymentField": ["keep"]})
    item_created_at = store.db.documents[(item_path, item_id)]["createdAt"]
    payment_created_at = store.db.documents[(payment_path, payment_id)]["createdAt"]

    response = test_client.put(
        f"/api/owner-scope/items/{item_id}",
        json={**_item_payload(name="Updated"), "amountPaid": "1600"},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200
    assert store.db.documents[(item_path, item_id)]["createdAt"] == item_created_at
    assert store.db.documents[(item_path, item_id)]["unrelatedItemField"] == {"keep": True}
    assert store.db.documents[(payment_path, payment_id)]["createdAt"] == payment_created_at
    assert store.db.documents[(payment_path, payment_id)]["unrelatedPaymentField"] == ["keep"]


def test_owner_scope_browser_source_has_no_direct_firestore_mutations():
    from pathlib import Path
    import re

    script = (Path(__file__).parents[1] / "static/owner_scope.js").read_text(
        encoding="utf-8"
    )
    assert not re.search(r"\b(?:addDoc|setDoc|updateDoc|deleteDoc)\s*\(", script)
    assert "firebase/firestore" not in script
    assert 'method: "POST"' in script
    assert 'method: "PUT"' in script
    assert 'method: "DELETE"' in script
    assert "sessionStorage.setItem(createRequestStorageKey" in script
    assert "initializeOwnerScopeCreateRequestId({ forceNew: true })" in script


def test_date_only_edit_preserves_owner_scope_paid_total(client):
    test_client, store = client
    item_id, payment_id = _add_historical_paid_item(store)
    before = test_client.get("/api/owner-scope").get_json()
    total_before = sum(payment["amount"] for payment in before["payments"])

    response = test_client.put(
        f"/api/owner-scope/items/{item_id}",
        json={
            **_item_payload(name="Historical expense"),
            "paymentDate": "2025-02-03",
            "amountPaid": "1250",
            "paymentMode": "Bank Transfer",
        },
        headers=_mutation_headers(),
    )

    assert response.status_code == 200
    after = test_client.get("/api/owner-scope").get_json()
    payment = next(payment for payment in after["payments"] if payment["id"] == payment_id)
    assert payment["date"] == "2025-02-03"
    assert payment["amount"] == 1250
    assert sum(payment["amount"] for payment in after["payments"]) == total_before


@pytest.mark.parametrize(
    ("payment_date", "amount"),
    [
        ("2025-02-03", "-1"),
        ("2025-02-03", "not-a-number"),
        ("2025-02-30", "100"),
        ("03/02/2025", "100"),
    ],
)
def test_paid_item_edit_rejects_invalid_payment_amount_or_date_without_writes(
    client, payment_date, amount
):
    test_client, store = client
    item_id, payment_id = _add_historical_paid_item(store)
    payment_key = (
        f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments",
        payment_id,
    )
    payment_before = deepcopy(store.db.documents[payment_key])
    store.db.writes.clear()

    response = test_client.put(
        f"/api/owner-scope/items/{item_id}",
        json={
            **_item_payload(),
            "paymentDate": payment_date,
            "amountPaid": amount,
            "paymentMode": "",
        },
        headers=_mutation_headers(),
    )

    assert response.status_code == 400
    assert store.db.documents[payment_key] == payment_before
    assert store.db.writes == []


def test_owner_scope_history_shows_required_payment_columns_and_total():
    from pathlib import Path

    root = Path(__file__).parents[1]
    template = (root / "templates/owner_scope.html").read_text(encoding="utf-8")
    script = (root / "static/owner_scope.js").read_text(encoding="utf-8")
    for column in ("Date", "Amount", "Payment Mode", "Remarks"):
        assert f"<th scope=\"col\">{column}</th>" in template
    assert 'id="ownerScopeHistoryTotal"' in template
    assert "page.historyTotal.textContent = formatMoney(" in script
    assert "formatDate(payment.date)" in script
    assert "formatMoney(payment.amount)" in script
    assert "payment.paymentMode ||" in script
    assert "payment.remarks ||" in script
    assert 'month: "2-digit"' in script
    assert "day: \"2-digit\"" in script


def test_owner_scope_item_crud_leaves_legacy_trade_unread_and_unchanged(client):
    test_client, store = client
    database = store.db
    legacy_key = (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID)
    legacy_before = deepcopy(database.documents[legacy_key])
    transaction_ids_before = [payment["id"] for payment in legacy_before["paymentTransactions"]]
    count_before = len(legacy_before["paymentTransactions"])
    total_before = sum(payment["amount"] for payment in legacy_before["paymentTransactions"])

    read_response = test_client.get("/api/owner-scope")
    assert read_response.status_code == 200
    read_data = read_response.get_json()
    assert read_data["metadata"]["legacySource"]["documentId"] == LEGACY_OWNER_ACCOUNT_DOCUMENT_ID
    assert read_data["items"] == []
    assert read_data["payments"] == []
    assert "legacy" not in read_data
    assert not any(path == LEGACY_OWNER_ACCOUNT_COLLECTION for path, _ in database.reads)

    created = test_client.post(
        "/api/owner-scope/items",
        json={
            **_item_payload(),
            "paymentDate": "2026-09-29",
            "amountPaid": "2380",
            "paymentMode": "UPI",
        },
        headers=_mutation_headers(),
    )
    assert created.status_code == 201
    item_id = created.get_json()["id"]
    created_data = test_client.get("/api/owner-scope").get_json()
    assert len(created_data["items"]) == 1
    assert len(created_data["payments"]) == 1
    payment = created_data["payments"][0]
    assert payment["expenseItemId"] == item_id
    assert payment["date"] == "2026-09-29"
    assert payment["amount"] == 2380
    assert payment["paymentMode"] == "UPI"
    assert isinstance(payment["createdAt"], str)
    assert sum(entry["amount"] for entry in created_data["payments"]) == 2380

    updated = test_client.put(
        f"/api/owner-scope/items/{item_id}",
        json=_item_payload(name="Updated water connection"),
        headers=_mutation_headers(),
    )
    assert updated.status_code == 200

    deleted = test_client.delete(
        f"/api/owner-scope/items/{item_id}",
        headers=_mutation_headers(),
    )
    assert deleted.status_code == 200
    final_data = test_client.get("/api/owner-scope").get_json()
    assert final_data["items"] == []
    assert final_data["payments"] == []
    assert database.documents[legacy_key] == legacy_before
    assert [payment["id"] for payment in database.documents[legacy_key]["paymentTransactions"]] == transaction_ids_before


@pytest.mark.parametrize("payment_count", [0, 1, 3])
def test_deleting_item_removes_only_its_linked_payments_and_updates_totals(
    client, payment_count
):
    test_client, store = client
    database = store.db
    scope = database.collection(OWNER_SCOPE_COLLECTION).document(OWNER_SCOPE_DOCUMENT_ID)
    legacy_key = (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID)
    legacy_before = deepcopy(database.documents[legacy_key])
    migrated_items = {
        f"migrated-item-{index:02d}": {
            "name": f"Migrated item {index:02d}",
            "category": "site_expense",
            "remarks": "",
        }
        for index in range(35)
    }
    migrated_payments = {
        f"migrated-payment-{index:02d}": {
            "expenseItemId": f"migrated-item-{index:02d}",
            "date": "2025-01-01",
            "amount": 164148 if index < 34 else 164139,
            "paymentMode": "Bank Transfer",
        }
        for index in range(35)
    }
    for item_id, item_data in migrated_items.items():
        scope.collection("items").document(item_id).create(item_data)
    for payment_id, payment_data in migrated_payments.items():
        scope.collection("payments").document(payment_id).create(payment_data)
    migrated_records_before = {
        (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items", item_id): deepcopy(item_data)
        for item_id, item_data in migrated_items.items()
    }
    migrated_records_before.update({
        (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments", payment_id): deepcopy(payment_data)
        for payment_id, payment_data in migrated_payments.items()
    })

    target_id = "selected-item"
    scope.collection("items").document(target_id).create({
        "name": "Selected expense",
        "category": "site_expense",
        "remarks": "",
    })
    payments_collection = scope.collection("payments")
    target_payments = []
    for index in range(payment_count):
        payment_id = f"target-payment-{index}"
        payments_collection.document(payment_id).create({
            "expenseItemId": target_id,
            "date": "2025-01-01",
            "amount": 100 + index,
            "paymentMode": "Cash",
        })
        target_payments.append(payment_id)

    other_item_id = "other-item"
    scope.collection("items").document(other_item_id).create({
        "name": "Keep this expense",
        "category": "material",
        "remarks": "",
    })
    other_payment_id = "other-payment"
    payments_collection.document(other_payment_id).create({
        "expenseItemId": other_item_id,
        "date": "2025-01-02",
        "amount": 700,
        "paymentMode": "UPI",
    })

    before = test_client.get("/api/owner-scope").get_json()
    before_total = sum(payment["amount"] for payment in before["payments"])
    before_count = len(before["items"])
    deleted_total = sum(
        payment["amount"]
        for payment in before["payments"]
        if payment["expenseItemId"] == target_id
    )

    response = test_client.delete(
        f"/api/owner-scope/items/{target_id}",
        headers=_mutation_headers(),
    )

    assert response.status_code == 200
    after = test_client.get("/api/owner-scope").get_json()
    assert len(after["items"]) == before_count - 1
    assert sum(payment["amount"] for payment in after["payments"]) == before_total - deleted_total
    after_item_ids = {item["id"] for item in after["items"]}
    after_payment_ids = {payment["id"] for payment in after["payments"]}
    assert after_item_ids == set(migrated_items) | {other_item_id}
    assert after_payment_ids == set(migrated_payments) | {other_payment_id}
    assert all(payment["expenseItemId"] != target_id for payment in after["payments"])
    assert all(payment["expenseItemId"] in after_item_ids for payment in after["payments"])
    assert (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items", target_id) not in database.documents
    assert database.documents[
        (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items", other_item_id)
    ]["name"] == "Keep this expense"
    assert database.documents[
        (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments", other_payment_id)
    ]["amount"] == 700
    assert database.documents[legacy_key] == legacy_before
    assert all(
        (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments", payment_id)
        not in database.documents
        for payment_id in target_payments
    )
    for record_key, record_data in migrated_records_before.items():
        assert database.documents[record_key] == record_data


def test_item_edit_updates_only_the_selected_items_name_and_category(client):
    test_client, store = client
    first = test_client.post(
        "/api/owner-scope/items",
        json=_item_payload(name="First expense", category="mixed"),
        headers=_mutation_headers(),
    )
    second = test_client.post(
        "/api/owner-scope/items",
        json=_item_payload(name="Second expense", category="other"),
        headers=_mutation_headers(),
    )
    first_id = first.get_json()["id"]
    second_id = second.get_json()["id"]

    response = test_client.put(
        f"/api/owner-scope/items/{first_id}",
        json=_item_payload(name="Updated first", category="material"),
        headers=_mutation_headers(),
    )

    assert response.status_code == 200
    first_item = store.db.documents[
        (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items", first_id)
    ]
    second_item = store.db.documents[
        (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items", second_id)
    ]
    assert (first_item["name"], first_item["category"]) == (
        "Updated first",
        "material",
    )
    assert (second_item["name"], second_item["category"]) == (
        "Second expense",
        "other",
    )


@pytest.mark.parametrize("category", OWNER_SCOPE_CATEGORIES)
def test_all_nine_owner_scope_categories_can_be_created(client, category):
    test_client, _ = client
    response = test_client.post(
        "/api/owner-scope/items",
        json=_item_payload(category=category),
        headers=_mutation_headers(),
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "payload",
    [
        _item_payload(category="unapproved_category"),
        _item_payload(name="   "),
        {"name": "Missing category", "remarks": ""},
        {**_item_payload(), "budgetAmount": 100},
    ],
)
def test_invalid_owner_scope_items_are_rejected_without_writes(client, payload):
    test_client, store = client
    response = test_client.post(
        "/api/owner-scope/items",
        json=payload,
        headers=_mutation_headers(),
    )
    assert response.status_code == 400
    assert store.db.writes == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("paymentDate", ""),
        ("amountPaid", ""),
        ("amountPaid", "-1"),
        ("amountPaid", "invalid"),
    ],
)
def test_new_items_require_a_valid_payment_date_and_nonnegative_amount(
    client, field, value
):
    test_client, store = client
    payload = {**_item_payload(), field: value}

    response = test_client.post(
        "/api/owner-scope/items",
        json=payload,
        headers=_mutation_headers(),
    )

    assert response.status_code == 400
    assert store.db.writes == []


def test_items_with_payment_history_can_be_deleted_with_their_payment(client):
    test_client, store = client
    created = test_client.post(
        "/api/owner-scope/items",
        json=_item_payload(),
        headers=_mutation_headers(),
    )
    item_id = created.get_json()["id"]
    payment_ref = (
        store.db.collection(OWNER_SCOPE_COLLECTION)
        .document(OWNER_SCOPE_DOCUMENT_ID)
        .collection("payments")
        .document("future-payment")
    )
    payment_ref.create({"expenseItemId": item_id, "amount": 25})

    response = test_client.delete(
        f"/api/owner-scope/items/{item_id}",
        headers=_mutation_headers(),
    )
    assert response.status_code == 200
    assert test_client.get("/api/owner-scope").get_json()["items"] == []
    assert test_client.get("/api/owner-scope").get_json()["payments"] == []
    assert (
        store.db.collection(OWNER_SCOPE_COLLECTION)
        .document(OWNER_SCOPE_DOCUMENT_ID)
        .collection("items")
        .document(item_id)
        .get()
        .exists is False
    )


def test_delete_rejects_invalid_and_nonexistent_item_ids_safely(client):
    test_client, store = client
    invalid = test_client.delete(
        "/api/owner-scope/items/invalid%00id",
        headers=_mutation_headers(),
    )
    missing = test_client.delete(
        "/api/owner-scope/items/does-not-exist",
        headers=_mutation_headers(),
    )
    assert invalid.status_code == 400
    assert missing.status_code == 404
    assert store.db.writes == []


def test_deleting_one_migrated_owner_scope_item_preserves_the_other_34(client):
    test_client, store = client
    database = store.db
    scope = database.collection(OWNER_SCOPE_COLLECTION).document(OWNER_SCOPE_DOCUMENT_ID)
    items = scope.collection("items")
    payments = scope.collection("payments")
    legacy_key = (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID)
    legacy_before = deepcopy(database.documents[legacy_key])
    labour_trade_key = ("trades", "normal-labour-trade")
    database.documents[labour_trade_key] = {
        "name": "Carpentry",
        "paymentTransactions": [{"id": "normal-payment", "amount": 2400}],
    }
    labour_trade_before = deepcopy(database.documents[labour_trade_key])

    for index in range(35):
        item_id = f"migrated-{index:02d}"
        payment_id = f"legacy-payment-{index:02d}"
        items.document(item_id).create({
            "name": f"Migrated expense {index:02d}",
            "category": "site_expense",
            "remarks": "",
            "sourceLegacyTransactionId": payment_id,
        })
        payments.document(payment_id).create({
            "expenseItemId": item_id,
            "sourceLegacyTransactionId": payment_id,
            "sourceTransactionId": payment_id,
            "date": "2025-01-01",
            "amount": 164148 if index < 34 else 164139,
            "paymentMode": "Bank Transfer",
        })

    before = test_client.get("/api/owner-scope").get_json()
    response = test_client.delete(
        "/api/owner-scope/items/migrated-00",
        headers=_mutation_headers(),
    )

    assert response.status_code == 200
    after = test_client.get("/api/owner-scope").get_json()
    assert len(before["items"]) == 35
    assert len(before["payments"]) == 35
    assert len(after["items"]) == 34
    assert len(after["payments"]) == 34
    assert sum(payment["amount"] for payment in after["payments"]) == (
        sum(payment["amount"] for payment in before["payments"]) - 164148
    )
    assert "migrated-00" not in {item["id"] for item in after["items"]}
    assert "legacy-payment-00" not in {payment["id"] for payment in after["payments"]}
    assert {item["id"] for item in after["items"]} == {
        f"migrated-{index:02d}" for index in range(1, 35)
    }
    assert database.documents[legacy_key] == legacy_before
    assert database.documents[labour_trade_key] == labour_trade_before


def test_owner_scope_mutations_require_csrf_and_never_offer_payment_creation(client):
    test_client, store = client
    response = test_client.post("/api/owner-scope/items", json=_item_payload())
    assert response.status_code == 403
    assert store.db.writes == []
    assert test_client.post("/api/owner-scope/payments").status_code == 404
