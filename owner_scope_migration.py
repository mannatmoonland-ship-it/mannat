from datetime import datetime, timezone
from decimal import Decimal

from owner_scope import (
    LEGACY_OWNER_ACCOUNT_COLLECTION,
    LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    OWNER_SCOPE_COLLECTION,
    OWNER_SCOPE_DOCUMENT_ID,
)


MIGRATION_ID = "owners-account-trade-v1"
OWNER_ACCOUNT_ITEM_ID = "legacy-owners-account"
EXPECTED_PAYMENT_COUNT = 35
EXPECTED_PAYMENT_TOTAL = Decimal("5745171")
EXPECTED_CONTRACT_AMOUNT = 10000000


def _normalize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _source_snapshot(db, baseline):
    source_ref = db.collection(LEGACY_OWNER_ACCOUNT_COLLECTION).document(
        LEGACY_OWNER_ACCOUNT_DOCUMENT_ID
    )
    source_snapshot = source_ref.get()
    if not source_snapshot.exists:
        raise RuntimeError("The legacy Owner Account trade was not found.")
    source = source_snapshot.to_dict() or {}

    if source_snapshot.id != baseline.get("legacyDocumentId"):
        raise RuntimeError("Legacy Owner Account document ID differs from the baseline.")
    if _normalize(source) != baseline.get("legacyDocument"):
        raise RuntimeError("Legacy Owner Account data differs from the recorded baseline.")
    if source.get("name") != "OWNERS ACCOUNT":
        raise RuntimeError("The source document is not the expected OWNERS ACCOUNT trade.")

    contracts = source.get("contractItems")
    if (
        not isinstance(contracts, list)
        or len(contracts) != 1
        or contracts[0].get("name") != "Owners Account"
        or Decimal(str(contracts[0].get("amount", -1))) != EXPECTED_CONTRACT_AMOUNT
    ):
        raise RuntimeError("The source contract item does not match the approved migration.")

    transactions = source.get("paymentTransactions")
    if not isinstance(transactions, list) or len(transactions) != EXPECTED_PAYMENT_COUNT:
        raise RuntimeError("The legacy payment transaction count is not 35.")
    transaction_ids = [transaction.get("id") for transaction in transactions]
    if (
        any(not transaction_id for transaction_id in transaction_ids)
        or len(set(transaction_ids)) != EXPECTED_PAYMENT_COUNT
        or transaction_ids != baseline.get("legacyTransactionIds")
    ):
        raise RuntimeError("Legacy payment transaction IDs differ from the baseline.")

    total = sum(
        (Decimal(str(transaction.get("amount", 0))) for transaction in transactions),
        Decimal(0),
    )
    if total != EXPECTED_PAYMENT_TOTAL or str(total) != baseline.get("legacyPaymentTotal"):
        raise RuntimeError("Legacy payment total differs from ₹57,45,171 or the baseline.")
    return source, contracts[0], transactions


def _item_payload(contract, timestamp):
    return {
        "name": contract["name"],
        "category": "mixed",
        "budgetAmount": contract["amount"],
        "remarks": (
            "Migrated from the OWNERS ACCOUNT trade; historical expenses are "
            "kept together as Mixed."
        ),
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "migrationId": MIGRATION_ID,
        "sourceCollection": LEGACY_OWNER_ACCOUNT_COLLECTION,
        "sourceDocumentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        "sourceContractItemId": contract["id"],
    }


def _payment_payload(transaction, item_id, timestamp):
    return {
        "expenseItemId": item_id,
        "sourceLegacyTransactionId": transaction["id"],
        "sourceTransactionId": transaction["id"],
        "migrationId": MIGRATION_ID,
        "sourceCollection": LEGACY_OWNER_ACCOUNT_COLLECTION,
        "sourceDocumentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        "date": transaction.get("date"),
        "amount": transaction.get("amount"),
        "paymentMode": transaction.get("paymentMode"),
        "remarks": transaction.get("remarks"),
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }


def _assert_migrated_data(db, contract, transactions):
    scope_ref = db.collection(OWNER_SCOPE_COLLECTION).document(OWNER_SCOPE_DOCUMENT_ID)
    item_snapshots = list(scope_ref.collection("items").stream())
    payment_snapshots = list(scope_ref.collection("payments").stream())
    if len(item_snapshots) != 1 or item_snapshots[0].id != OWNER_ACCOUNT_ITEM_ID:
        raise RuntimeError("Owner Scope must contain exactly the one migrated account item.")
    if len(payment_snapshots) != EXPECTED_PAYMENT_COUNT:
        raise RuntimeError("Owner Scope must contain exactly 35 migrated payments.")

    item = item_snapshots[0].to_dict() or {}
    expected_item = _item_payload(contract, item.get("createdAt"))
    for key, value in expected_item.items():
        if key not in {"createdAt", "updatedAt"} and item.get(key) != value:
            raise RuntimeError(f"Migrated Owner Scope item field {key!r} failed verification.")
    if not item.get("createdAt") or not item.get("updatedAt"):
        raise RuntimeError("Migrated Owner Scope item timestamps are missing.")

    payments_by_id = {snapshot.id: snapshot.to_dict() or {} for snapshot in payment_snapshots}
    source_ids = {transaction["id"] for transaction in transactions}
    if set(payments_by_id) != source_ids:
        raise RuntimeError("Migrated Owner Scope payment IDs do not match the source IDs.")

    total = Decimal(0)
    for transaction in transactions:
        payment_id = transaction["id"]
        payment = payments_by_id[payment_id]
        expected_payment = _payment_payload(transaction, OWNER_ACCOUNT_ITEM_ID, payment.get("createdAt"))
        for key, value in expected_payment.items():
            if key not in {"createdAt", "updatedAt"} and payment.get(key) != value:
                raise RuntimeError(
                    f"Migrated payment {payment_id} field {key!r} failed verification."
                )
        if not payment.get("createdAt") or not payment.get("updatedAt"):
            raise RuntimeError(f"Migrated payment {payment_id} timestamps are missing.")
        total += Decimal(str(payment.get("amount", 0)))
    if total != EXPECTED_PAYMENT_TOTAL:
        raise RuntimeError("Migrated Owner Scope payment total is not ₹57,45,171.")

    return {
        "itemCount": len(item_snapshots),
        "paymentCount": len(payment_snapshots),
        "budgetAmount": item.get("budgetAmount"),
        "paidTotal": int(total),
        "remaining": int(Decimal(str(item["budgetAmount"])) - total),
        "transactionIds": sorted(payments_by_id),
    }


def migrate_owner_account(db, baseline, apply=False):
    source, contract, transactions = _source_snapshot(db, baseline)
    scope_ref = db.collection(OWNER_SCOPE_COLLECTION).document(OWNER_SCOPE_DOCUMENT_ID)
    scope_snapshot = scope_ref.get()
    scope_metadata = scope_snapshot.to_dict() if scope_snapshot.exists else {}
    if (
        scope_metadata.get("name") != "Owner Scope"
        or scope_metadata.get("legacySource")
        != {
            "collection": LEGACY_OWNER_ACCOUNT_COLLECTION,
            "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        }
    ):
        raise RuntimeError("Owner Scope metadata is missing or has an unexpected legacy reference.")
    item_ref = scope_ref.collection("items").document(OWNER_ACCOUNT_ITEM_ID)
    item_snapshots = list(scope_ref.collection("items").stream())
    payment_snapshots = list(scope_ref.collection("payments").stream())

    already_migrated = (
        len(item_snapshots) == 1
        and item_snapshots[0].id == OWNER_ACCOUNT_ITEM_ID
        and len(payment_snapshots) == EXPECTED_PAYMENT_COUNT
    )
    if already_migrated:
        verification = _assert_migrated_data(db, contract, transactions)
        return {"created": False, **verification}
    if item_snapshots or payment_snapshots:
        raise RuntimeError(
            "Owner Scope destination is partially populated or contains unrelated financial data."
        )
    if not apply:
        return {
            "created": False,
            "dryRun": True,
            "sourceTransactionCount": len(transactions),
            "sourcePaymentTotal": int(EXPECTED_PAYMENT_TOTAL),
            "targetItemId": OWNER_ACCOUNT_ITEM_ID,
        }

    timestamp = datetime.now(timezone.utc)
    batch = db.batch()
    batch.create(item_ref, _item_payload(contract, timestamp))
    for transaction in transactions:
        payment_ref = scope_ref.collection("payments").document(transaction["id"])
        batch.create(payment_ref, _payment_payload(transaction, OWNER_ACCOUNT_ITEM_ID, timestamp))
    batch.commit()

    verification = _assert_migrated_data(db, contract, transactions)
    _source_snapshot(db, baseline)
    return {"created": True, **verification}
