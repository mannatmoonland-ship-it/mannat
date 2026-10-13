from copy import deepcopy
from decimal import Decimal

import pytest

from owner_scope import (
    LEGACY_OWNER_ACCOUNT_COLLECTION,
    LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    OWNER_SCOPE_COLLECTION,
    OWNER_SCOPE_DOCUMENT_ID,
)
from scripts.finalize_owner_scope import (
    EXPECTED_PAYMENT_TOTAL,
    OWNER_ACCOUNT_ITEM_ID,
    finalize_owner_scope,
)


def source_data():
    transactions = [
        {
            "id": f"transaction-{index:02d}",
            "date": f"2025-01-{index + 1:02d}",
            "amount": 164148 if index < 34 else 164139,
            "paymentMode": "Bank Transfer",
            "remarks": f"Expense {index}",
        }
        for index in range(35)
    ]
    return {
        "name": "OWNERS ACCOUNT",
        "contractItems": [{"id": "contract-1", "amount": 10000000}],
        "paymentTransactions": transactions,
    }


class FakeRest:
    def __init__(self):
        source = source_data()
        self.documents = {
            f"{LEGACY_OWNER_ACCOUNT_COLLECTION}/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}": source,
            f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}": {
                "name": "Owner Scope",
                "legacySource": {
                    "collection": LEGACY_OWNER_ACCOUNT_COLLECTION,
                    "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
                },
            },
            (
                f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items/"
                f"{OWNER_ACCOUNT_ITEM_ID}"
            ): {
                "name": "Owners Account",
                "category": "mixed",
                "budgetAmount": None,
                "sourceLegacyTradeId": None,
                "remarks": "Existing item",
            },
        }
        for transaction in source["paymentTransactions"]:
            self.documents[
                f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments/"
                f"{transaction['id']}"
            ] = {
                "expenseItemId": OWNER_ACCOUNT_ITEM_ID,
                "sourceTransactionId": transaction["id"],
                "date": transaction["date"],
                "amount": transaction["amount"],
                "paymentMode": transaction["paymentMode"],
                "remarks": transaction["remarks"],
            }
        self.writes = []

    def get_document(self, path):
        return "stable-update-time", deepcopy(self.documents[path])

    def list_documents(self, path):
        prefix = f"{path}/"
        return [
            (key[len(prefix):], deepcopy(value))
            for key, value in self.documents.items()
            if key.startswith(prefix) and "/" not in key[len(prefix):]
        ]

    def patch_document(self, path, fields):
        if path not in self.documents:
            raise AssertionError(f"Update of a missing document: {path}")
        self.documents[path].update(deepcopy(fields))
        self.writes.append(path)


def test_finalization_repairs_existing_records_without_creating_documents():
    rest = FakeRest()
    initial_paths = set(rest.documents)

    result = finalize_owner_scope(rest)

    item = rest.documents[
        f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items/"
        f"{OWNER_ACCOUNT_ITEM_ID}"
    ]
    payments = rest.list_documents(
        f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments"
    )
    assert result["alreadyFinalized"] is False
    assert result["itemCount"] == 1
    assert result["paymentCount"] == 35
    assert result["sourceTotal"] == int(EXPECTED_PAYMENT_TOTAL)
    assert result["ownerScopeTotal"] == 5745171
    assert result["remaining"] == 4254829
    assert result["markersAdded"] == 35
    assert item["budgetAmount"] == 10000000
    assert item["sourceLegacyTradeId"] == LEGACY_OWNER_ACCOUNT_DOCUMENT_ID
    assert all(
        payment["sourceLegacyTransactionId"] == payment["sourceTransactionId"]
        for _, payment in payments
    )
    assert set(rest.documents) == initial_paths
    assert len(rest.writes) == 36

    result_again = finalize_owner_scope(rest)

    assert result_again["alreadyFinalized"] is True
    assert result_again["writes"] == []
    assert result_again["markersAdded"] == 0
    assert len(rest.writes) == 36
    assert set(rest.documents) == initial_paths


@pytest.mark.parametrize(
    "change",
    [
        lambda rest: rest.documents[
            f"{LEGACY_OWNER_ACCOUNT_COLLECTION}/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}"
        ].update(name="Unexpected trade"),
        lambda rest: rest.documents[
            f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments/transaction-00"
        ].update(amount=1),
    ],
)
def test_finalization_refuses_unexpected_data_without_writes(change):
    rest = FakeRest()
    change(rest)

    with pytest.raises(RuntimeError):
        finalize_owner_scope(rest)

    assert rest.writes == []


def test_finalization_preserves_financial_fields_and_legacy_source():
    rest = FakeRest()
    legacy_before = deepcopy(
        rest.documents[
            f"{LEGACY_OWNER_ACCOUNT_COLLECTION}/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}"
        ]
    )
    payment_before = {
        key: deepcopy(value)
        for key, value in rest.documents.items()
        if "/payments/" in key
    }

    finalize_owner_scope(rest)

    assert rest.documents[
        f"{LEGACY_OWNER_ACCOUNT_COLLECTION}/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}"
    ] == legacy_before
    for path, previous in payment_before.items():
        current = rest.documents[path]
        assert {
            field: current[field]
            for field in ("amount", "date", "paymentMode", "remarks", "expenseItemId")
        } == {
            field: previous[field]
            for field in ("amount", "date", "paymentMode", "remarks", "expenseItemId")
        }
        assert Decimal(str(current["amount"])) == Decimal(str(previous["amount"]))
