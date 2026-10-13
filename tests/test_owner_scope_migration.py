from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from owner_scope import (
    LEGACY_OWNER_ACCOUNT_COLLECTION,
    LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    OWNER_SCOPE_COLLECTION,
    OWNER_SCOPE_DOCUMENT_ID,
    get_owner_scope_data,
)
from owner_scope_migration import (
    OWNER_ACCOUNT_ITEM_ID,
    migrate_owner_account,
)


def _source_data():
    transactions = [
        {
            "id": f"transaction-{index:02d}",
            "date": f"2025-01-{index + 1:02d}",
            "amount": 164148 if index < 34 else 164139,
            "paymentMode": "Bank Transfer",
            "remarks": f"Mixed expense {index}",
            "type": "mobilization_advance",
            "createdAt": f"2025-01-{index + 1:02d}T12:00:00Z",
        }
        for index in range(35)
    ]
    return {
        "name": "OWNERS ACCOUNT",
        "contractItems": [
            {
                "id": "source-contract-item",
                "name": "Owners Account",
                "amount": 10000000,
            }
        ],
        "workerCategories": [{"id": "legacy-worker"}],
        "paymentTransactions": transactions,
    }


class Snapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self._data = deepcopy(data)
        self.exists = data is not None

    def to_dict(self):
        return deepcopy(self._data)


class DocumentReference:
    def __init__(self, database, collection_path, document_id):
        self.database = database
        self.collection_path = collection_path
        self.id = document_id
        self.key = (collection_path, document_id)

    def get(self):
        return Snapshot(self.id, self.database.documents.get(self.key))

    def create(self, data):
        if self.key in self.database.documents:
            raise RuntimeError(f"Document already exists: {self.key}")
        self.database.documents[self.key] = deepcopy(data)
        self.database.writes.append(self.key)

    def collection(self, name):
        return CollectionReference(self.database, f"{self.collection_path}/{self.id}/{name}")


class CollectionReference:
    def __init__(self, database, path):
        self.database = database
        self.path = path

    def document(self, document_id):
        return DocumentReference(self.database, self.path, document_id)

    def stream(self):
        return [
            Snapshot(document_id, data)
            for (path, document_id), data in self.database.documents.items()
            if path == self.path
        ]


class WriteBatch:
    def __init__(self, database):
        self.database = database
        self.pending = []

    def create(self, reference, data):
        self.pending.append((reference, deepcopy(data)))

    def commit(self):
        if any(reference.key in self.database.documents for reference, _ in self.pending):
            raise RuntimeError("A document in this atomic batch already exists.")
        for reference, data in self.pending:
            self.database.documents[reference.key] = data
            self.database.writes.append(reference.key)


class Database:
    def __init__(self):
        self.legacy = _source_data()
        self.documents = {
            (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID): deepcopy(self.legacy),
            (OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID): {
                "name": "Owner Scope",
                "legacySource": {
                    "collection": LEGACY_OWNER_ACCOUNT_COLLECTION,
                    "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
                },
            },
        }
        self.writes = []

    def collection(self, name):
        return CollectionReference(self, name)

    def batch(self):
        return WriteBatch(self)


@pytest.fixture
def database_and_baseline():
    database = Database()
    source = deepcopy(database.legacy)
    ids = [transaction["id"] for transaction in source["paymentTransactions"]]
    baseline = {
        "legacyDocumentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        "legacyDocument": source,
        "legacyTransactionIds": ids,
        "legacyPaymentTotal": "5745171",
    }
    return database, baseline


def test_migration_dry_run_records_expected_source_without_writes(database_and_baseline):
    database, baseline = database_and_baseline
    result = migrate_owner_account(database, baseline)
    assert result == {
        "created": False,
        "dryRun": True,
        "sourceTransactionCount": 35,
        "sourcePaymentTotal": 5745171,
        "targetItemId": OWNER_ACCOUNT_ITEM_ID,
    }
    assert database.writes == []


def test_migration_preserves_source_fields_and_is_idempotent(database_and_baseline):
    database, baseline = database_and_baseline
    legacy_before = deepcopy(database.legacy)
    result = migrate_owner_account(database, baseline, apply=True)

    assert result["created"] is True
    assert result["itemCount"] == 1
    assert result["paymentCount"] == 35
    assert result["budgetAmount"] == 10000000
    assert result["paidTotal"] == 5745171
    assert result["remaining"] == 4254829
    item = database.documents[
        (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items", OWNER_ACCOUNT_ITEM_ID)
    ]
    assert item["name"] == "Owners Account"
    assert item["category"] == "mixed"
    assert item["budgetAmount"] == 10000000

    payments = {
        document_id: data
        for (path, document_id), data in database.documents.items()
        if path == f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments"
    }
    assert len(payments) == 35
    for transaction in legacy_before["paymentTransactions"]:
        payment = payments[transaction["id"]]
        assert payment["sourceTransactionId"] == transaction["id"]
        assert payment["sourceLegacyTransactionId"] == transaction["id"]
        assert payment["expenseItemId"] == OWNER_ACCOUNT_ITEM_ID
        for key in ("date", "amount", "paymentMode", "remarks"):
            assert payment[key] == transaction.get(key)
    assert sum(Decimal(str(payment["amount"])) for payment in payments.values()) == Decimal("5745171")
    assert database.documents[(LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID)] == legacy_before
    api_data = get_owner_scope_data(database)
    assert api_data["items"][0]["name"] == "Owners Account"
    assert api_data["items"][0]["category"] == "mixed"
    assert len(api_data["payments"]) == 35
    assert sum(payment["amount"] for payment in api_data["payments"]) == 5745171
    assert api_data["payments"][0]["sourceTransactionId"]
    writes_after_first_run = list(database.writes)

    second_result = migrate_owner_account(database, baseline, apply=True)
    assert second_result["created"] is False
    assert second_result["itemCount"] == 1
    assert second_result["paymentCount"] == 35
    assert second_result["paidTotal"] == 5745171
    assert database.writes == writes_after_first_run


def test_migration_refuses_changed_baseline_or_populated_destination(database_and_baseline):
    database, baseline = database_and_baseline
    changed_baseline = deepcopy(baseline)
    changed_baseline["legacyDocument"]["paymentTransactions"][0]["amount"] += 1
    with pytest.raises(RuntimeError, match="differs from the recorded baseline"):
        migrate_owner_account(database, changed_baseline, apply=True)
    assert database.writes == []

    database.documents[
        (f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items", "unrelated-item")
    ] = {"name": "Existing item"}
    with pytest.raises(RuntimeError, match="partially populated"):
        migrate_owner_account(database, baseline, apply=True)
    assert database.writes == []
