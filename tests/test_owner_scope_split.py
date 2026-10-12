from copy import deepcopy
from decimal import Decimal

import pytest

from owner_scope import (
    LEGACY_OWNER_ACCOUNT_COLLECTION,
    LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    OWNER_SCOPE_COLLECTION,
    OWNER_SCOPE_DOCUMENT_ID,
)
from scripts.finalize_owner_scope import EXPECTED_PAYMENT_TOTAL
from scripts.split_owner_scope_items import (
    LEGACY_ITEM_ID,
    _read_state,
    dry_run_report,
    item_id_for_transaction,
    migrate_split_items,
)


def make_source():
    transactions = [
        {
            "id": f"transaction-{index:02d}",
            "date": f"2025-01-{index + 1:02d}",
            "amount": 164148 if index < 34 else 164139,
            "paymentMode": "Bank Transfer",
            "remarks": f"Expense detail {index}",
        }
        for index in range(35)
    ]
    return {
        "name": "OWNERS ACCOUNT",
        "contractItems": [{"name": "Owners Account", "id": "cap-1", "amount": 10000000}],
        "paymentTransactions": transactions,
    }


class FakeRest:
    def __init__(self):
        self.source = make_source()
        self.documents = {
            f"{LEGACY_OWNER_ACCOUNT_COLLECTION}/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}": deepcopy(self.source),
            f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}": {
                "name": "Owner Scope",
                "legacySource": {
                    "collection": LEGACY_OWNER_ACCOUNT_COLLECTION,
                    "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
                },
            },
            f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items/{LEGACY_ITEM_ID}": {
                "name": "Owners Account",
                "category": "mixed",
                "budgetAmount": None,
                "sourceLegacyTradeId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
            },
        }
        for transaction in self.source["paymentTransactions"]:
            self.documents[
                f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments/{transaction['id']}"
            ] = {
                "expenseItemId": LEGACY_ITEM_ID,
                "sourceLegacyTransactionId": transaction["id"],
                "sourceTransactionId": transaction["id"],
                "date": transaction["date"],
                "amount": transaction["amount"],
                "paymentMode": transaction["paymentMode"],
                "remarks": transaction["remarks"],
            }
        self.writes = []
        self.base_url = "https://firestore.googleapis.com/v1/documents"
        self.session = self

    def get_document(self, path):
        return "source-update-time", deepcopy(self.documents[path])

    def list_documents(self, path):
        prefix = f"{path}/"
        return [
            (key[len(prefix):], deepcopy(data))
            for key, data in self.documents.items()
            if key.startswith(prefix) and "/" not in key[len(prefix):]
        ]

    def patch_document(self, path, fields):
        if path not in self.documents:
            raise AssertionError(f"Patch of missing document: {path}")
        self.documents[path].update(deepcopy(fields))
        self.writes.append((path, deepcopy(fields)))

    def create_document(self, collection_path, document_id, fields):
        path = f"{collection_path}/{document_id}"
        if path in self.documents:
            raise AssertionError(f"Duplicate create attempted: {path}")
        self.documents[path] = deepcopy(fields)
        self.writes.append((path, "create"))

    def delete(self, url, timeout):
        path = url.split("/documents/", 1)[1]
        assert path in self.documents
        del self.documents[path]
        self.writes.append((path, "delete"))
        return FakeResponse()


class FakeResponse:
    def raise_for_status(self):
        return None


def test_dry_run_reports_existing_transactions_without_writes():
    rest = FakeRest()

    result = migrate_split_items(rest)

    assert result == {
        "dryRun": True,
        "sourceTransactionCount": 35,
        "sourceTotal": 5745171,
        "existingItemCount": 1,
        "existingPaymentCount": 35,
        "proposedItemCount": 35,
        "proposedPaymentCount": 35,
        "itemsToCreate": 35,
        "paymentsToReassign": 35,
        "duplicateCount": 0,
        "missingCount": 0,
        "amountDifference": 0,
        "scopeBudgetBefore": None,
        "scopeBudgetAfter": 10000000,
    }
    assert rest.writes == []


def test_split_creates_deterministic_mixed_items_and_reuses_all_payments():
    rest = FakeRest()
    source_before = deepcopy(rest.source)
    financial_before = {
        path: {
            key: value
            for key, value in payment.items()
            if key != "expenseItemId"
        }
        for path, payment in rest.documents.items()
        if "/payments/" in path
    }

    result = migrate_split_items(rest, apply=True)
    state = _read_state(rest)

    assert result["itemCount"] == 35
    assert result["paymentCount"] == 35
    assert result["sourceTotal"] == 5745171
    assert result["destinationTotal"] == 5745171
    assert result["scopeBudget"] == 10000000
    assert result["remaining"] == 4254829
    assert result["itemsCreated"] == 35
    assert result["paymentsReassigned"] == 35
    assert LEGACY_ITEM_ID not in state["itemsById"]
    assert len(state["itemsBySource"]) == 35
    assert len(state["paymentsBySource"]) == 35
    assert len(set(state["itemsBySource"].values())) == 35
    assert all(item["category"] == "mixed" for _, item in state["items"])
    assert all(item["budgetAmount"] is None for _, item in state["items"])
    assert all(
        item_id_for_transaction(source_id) == item_id
        for source_id, item_id in state["itemsBySource"].items()
    )
    for source_id, item_id in state["itemsBySource"].items():
        payment_id, payment = state["paymentsBySource"][source_id]
        assert payment["expenseItemId"] == item_id
        assert payment_id == source_id
        assert sum(
            other["expenseItemId"] == item_id
            for _, other in state["payments"]
        ) == 1
    for path, original in financial_before.items():
        assert {
            key: rest.documents[path][key]
            for key in original
        } == original
    assert rest.documents[
        f"{LEGACY_OWNER_ACCOUNT_COLLECTION}/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}"
    ] == source_before
    assert sum(
        (Decimal(str(payment["amount"])) for _, payment in state["payments"]),
        Decimal(0),
    ) == EXPECTED_PAYMENT_TOTAL

    writes_before_second_run = list(rest.writes)
    second_result = migrate_split_items(rest, apply=True)
    assert second_result["itemsCreated"] == 0
    assert second_result["paymentsReassigned"] == 0
    assert second_result["itemCount"] == 35
    assert second_result["paymentCount"] == 35
    assert rest.writes == writes_before_second_run


@pytest.mark.parametrize("mutation", ["amount", "source_id", "duplicate"])
def test_preflight_refuses_mismatched_source_links_without_writes(mutation):
    rest = FakeRest()
    payment_path = (
        f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments/"
        "transaction-00"
    )
    if mutation == "amount":
        rest.documents[payment_path]["amount"] += 1
    elif mutation == "source_id":
        rest.documents[payment_path]["sourceLegacyTransactionId"] = "unknown"
    else:
        duplicate = deepcopy(rest.documents[payment_path])
        rest.documents[
            f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments/duplicate"
        ] = duplicate

    with pytest.raises(RuntimeError):
        migrate_split_items(rest, apply=True)

    assert rest.writes == []
