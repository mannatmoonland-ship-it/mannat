import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from requests import RequestException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from owner_scope import (
    LEGACY_OWNER_ACCOUNT_COLLECTION,
    LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    OWNER_SCOPE_CATEGORIES,
    OWNER_SCOPE_COLLECTION,
    OWNER_SCOPE_DOCUMENT_ID,
)
from scripts.finalize_owner_scope import (
    EXPECTED_CONTRACT_AMOUNT,
    EXPECTED_PAYMENT_COUNT,
    EXPECTED_PAYMENT_TOTAL,
    FIRESTORE_SCOPE,
    FirestoreRest,
    _source_data,
)


LEGACY_ITEM_ID = "legacy-owners-account"
ITEM_ID_PREFIX = "legacy-transaction-"
MAX_ITEM_NAME_LENGTH = 200


def item_id_for_transaction(transaction_id):
    if not isinstance(transaction_id, str) or not transaction_id or "/" in transaction_id:
        raise ValueError("A valid source transaction ID is required.")
    return f"{ITEM_ID_PREFIX}{transaction_id}"


def item_name_for_transaction(transaction, ordinal):
    remarks = transaction.get("remarks")
    if isinstance(remarks, str) and remarks.strip():
        return " ".join(remarks.split())[:MAX_ITEM_NAME_LENGTH]
    return f"Owner Expense {ordinal:02d}"


def _read_state(rest):
    source_time, source, contract, transactions = _source_data(rest)
    scope_path = f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}"
    scope_time, scope = rest.get_document(scope_path)
    if (
        scope.get("name") != "Owner Scope"
        or scope.get("legacySource")
        != {
            "collection": LEGACY_OWNER_ACCOUNT_COLLECTION,
            "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        }
    ):
        raise RuntimeError("Owner Scope metadata has an unexpected legacy reference.")
    scope_budget = scope.get("budgetAmount")
    if scope_budget not in (None, 10000000, 10000000.0):
        raise RuntimeError("Existing Owner Scope budget differs from the verified cap.")

    item_collection = f"{scope_path}/items"
    payment_collection = f"{scope_path}/payments"
    items = rest.list_documents(item_collection)
    payments = rest.list_documents(payment_collection)
    if len(payments) != EXPECTED_PAYMENT_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_PAYMENT_COUNT} existing payments; found {len(payments)}."
        )

    transactions_by_id = {transaction["id"]: transaction for transaction in transactions}
    expected_item_ids = {
        transaction_id: item_id_for_transaction(transaction_id)
        for transaction_id in transactions_by_id
    }
    items_by_source = {}
    items_by_id = dict(items)
    for item_id, item in items:
        source_id = item.get("sourceLegacyTransactionId")
        if source_id is None and item_id == LEGACY_ITEM_ID:
            continue
        if source_id not in transactions_by_id:
            raise RuntimeError(
                f"Unexpected Owner Scope item {item_id} has an unknown source identity."
            )
        if source_id in items_by_source:
            raise RuntimeError(f"Duplicate Owner Scope item for source ID {source_id}.")
        items_by_source[source_id] = item_id
        if item_id != expected_item_ids[source_id]:
            raise RuntimeError(
                f"Existing item for source {source_id} is not at its deterministic ID."
            )

    if any(
        item_id not in expected_item_ids.values() and item_id != LEGACY_ITEM_ID
        for item_id in items_by_id
    ):
        raise RuntimeError("Owner Scope contains an unrelated item; stopped.")

    payments_by_source = {}
    payment_total = Decimal(0)
    financial_mismatches = []
    for payment_id, payment in payments:
        source_id = payment.get("sourceLegacyTransactionId")
        if not source_id or source_id not in transactions_by_id:
            raise RuntimeError(
                f"Existing payment {payment_id} has a missing or unexpected source ID."
            )
        if source_id in payments_by_source:
            raise RuntimeError(f"Duplicate payment for source ID {source_id}.")
        payments_by_source[source_id] = (payment_id, payment)
        source_transaction = transactions_by_id[source_id]
        if payment_id != source_id:
            raise RuntimeError(
                f"Payment document ID {payment_id} differs from its source transaction ID."
            )
        if payment.get("sourceTransactionId") != source_id:
            raise RuntimeError(f"Payment {payment_id} has an unexpected sourceTransactionId.")
        for field in ("amount", "date", "paymentMode", "remarks"):
            if payment.get(field) != source_transaction.get(field):
                financial_mismatches.append((payment_id, field))
        payment_total += Decimal(str(payment.get("amount")))

    if set(payments_by_source) != set(transactions_by_id):
        raise RuntimeError("Existing Owner Scope payments do not match all source IDs.")
    if financial_mismatches:
        raise RuntimeError(
            f"Existing payment financial fields differ from source: {financial_mismatches[:3]}"
        )
    if payment_total != EXPECTED_PAYMENT_TOTAL:
        raise RuntimeError("Existing payment total differs from the verified source total.")

    for source_id, (payment_id, payment) in payments_by_source.items():
        linked_id = payment.get("expenseItemId")
        expected_id = expected_item_ids[source_id]
        if linked_id not in (LEGACY_ITEM_ID, expected_id):
            raise RuntimeError(
                f"Payment {payment_id} links to unexpected item {linked_id!r}."
            )

    return {
        "sourceTime": source_time,
        "scopeTime": scope_time,
        "source": source,
        "contract": contract,
        "transactions": transactions,
        "transactionsById": transactions_by_id,
        "scope": scope,
        "items": items,
        "itemsById": items_by_id,
        "itemsBySource": items_by_source,
        "payments": payments,
        "paymentsBySource": payments_by_source,
        "expectedItemIds": expected_item_ids,
        "paymentTotal": payment_total,
    }


def dry_run_report(state):
    item_ids = set(state["itemsBySource"])
    payment_sources = set(state["paymentsBySource"])
    return {
        "sourceTransactionCount": len(state["transactions"]),
        "sourceTotal": int(EXPECTED_PAYMENT_TOTAL),
        "existingItemCount": len(state["items"]),
        "existingPaymentCount": len(state["payments"]),
        "proposedItemCount": EXPECTED_PAYMENT_COUNT,
        "proposedPaymentCount": EXPECTED_PAYMENT_COUNT,
        "itemsToCreate": EXPECTED_PAYMENT_COUNT - len(item_ids),
        "paymentsToReassign": sum(
            payment.get("expenseItemId") != state["expectedItemIds"][source_id]
            for source_id, (_payment_id, payment) in state["paymentsBySource"].items()
        ),
        "duplicateCount": 0,
        "missingCount": EXPECTED_PAYMENT_COUNT - len(payment_sources),
        "amountDifference": int(
            state["paymentTotal"] - EXPECTED_PAYMENT_TOTAL
        ),
        "scopeBudgetBefore": state["scope"].get("budgetAmount"),
        "scopeBudgetAfter": int(EXPECTED_CONTRACT_AMOUNT),
    }


def _patch_with_progress(rest, path, fields, completed):
    try:
        rest.patch_document(path, fields)
    except RequestException as error:
        raise RuntimeError(
            f"REST update failed for {path}; confirmed prior updates: "
            f"{', '.join(completed) or 'none'}. Re-read before resuming."
        ) from error


def _delete_document(rest, path, completed):
    try:
        response = rest.session.delete(f"{rest.base_url}/{path}", timeout=30)
        response.raise_for_status()
    except RequestException as error:
        raise RuntimeError(
            f"REST delete failed for {path}; confirmed prior updates: "
            f"{', '.join(completed) or 'none'}. Re-read before resuming."
        ) from error


def migrate_split_items(rest, apply=False):
    state = _read_state(rest)
    report = dry_run_report(state)
    if not apply:
        return {"dryRun": True, **report}

    latest_source_time, _source, latest_contract, latest_transactions = _source_data(rest)
    if (
        latest_source_time != state["sourceTime"]
        or latest_contract != state["contract"]
        or latest_transactions != state["transactions"]
    ):
        raise RuntimeError("Source changed after preflight; no writes were started.")

    scope_path = f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}"
    completed = []
    if state["scope"].get("budgetAmount") is None:
        _patch_with_progress(
            rest,
            scope_path,
            {"budgetAmount": int(EXPECTED_CONTRACT_AMOUNT)},
            completed,
        )
        completed.append(f"{scope_path}:budgetAmount")

    created_items = 0
    for ordinal, transaction in enumerate(state["transactions"], start=1):
        source_id = transaction["id"]
        item_id = state["expectedItemIds"][source_id]
        if source_id in state["itemsBySource"]:
            continue
        if item_id in state["itemsById"]:
            raise RuntimeError(
                f"Deterministic item ID {item_id} exists without a matching source marker."
            )
        item_path = f"{scope_path}/items/{item_id}"
        item = {
            "name": item_name_for_transaction(transaction, ordinal),
            "category": "mixed",
            "budgetAmount": None,
            "remarks": "",
            "sourceLegacyTransactionId": source_id,
            "sourceLegacyTradeId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            rest.create_document(f"{scope_path}/items", item_id, item)
        except RequestException as error:
            raise RuntimeError(
                f"Could not create deterministic item {item_id}; confirmed prior "
                f"updates: {', '.join(completed) or 'none'}. Re-read before resuming."
            ) from error
        completed.append(item_path)
        created_items += 1

    reassigned_payments = 0
    for source_id, (payment_id, payment) in state["paymentsBySource"].items():
        target_item_id = state["expectedItemIds"][source_id]
        if payment.get("expenseItemId") == target_item_id:
            continue
        payment_path = f"{scope_path}/payments/{payment_id}"
        _update_time, latest_payment = rest.get_document(payment_path)
        for key in ("amount", "date", "paymentMode", "remarks",
                    "sourceLegacyTransactionId", "sourceTransactionId"):
            if latest_payment.get(key) != payment.get(key):
                raise RuntimeError(
                    f"Payment {payment_id} changed before reassignment; stopped."
                )
        if latest_payment.get("expenseItemId") != payment.get("expenseItemId"):
            raise RuntimeError(
                f"Payment {payment_id} link changed before reassignment; stopped."
            )
        _patch_with_progress(
            rest,
            payment_path,
            {"expenseItemId": target_item_id},
            completed,
        )
        completed.append(f"{payment_path}:expenseItemId")
        reassigned_payments += 1

    verified = _read_state(rest)
    _verify_split_state(
        verified,
        allow_legacy_item=LEGACY_ITEM_ID in verified["itemsById"],
    )

    legacy_path = f"{scope_path}/items/{LEGACY_ITEM_ID}"
    if LEGACY_ITEM_ID in verified["itemsById"]:
        if any(
            payment.get("expenseItemId") == LEGACY_ITEM_ID
            for _payment_id, payment in verified["payments"]
        ):
            raise RuntimeError("Combined item still has linked payments; not deleting it.")
        _delete_document(rest, legacy_path, completed)
        completed.append(f"{legacy_path}:deleted")

    final = _read_state(rest)
    _verify_split_state(final)
    return {
        "dryRun": False,
        "itemCount": len(final["items"]),
        "paymentCount": len(final["payments"]),
        "sourceTotal": int(EXPECTED_PAYMENT_TOTAL),
        "destinationTotal": int(final["paymentTotal"]),
        "scopeBudget": final["scope"].get("budgetAmount"),
        "remaining": int(
            Decimal(str(final["scope"]["budgetAmount"])) - final["paymentTotal"]
        ),
        "itemsCreated": created_items,
        "paymentsReassigned": reassigned_payments,
        "duplicates": 0,
        "missing": 0,
        "financialMismatches": 0,
        "writes": completed,
    }


def _verify_split_state(state, allow_legacy_item=False):
    legacy_present = LEGACY_ITEM_ID in state["itemsById"]
    expected_item_count = EXPECTED_PAYMENT_COUNT + int(
        allow_legacy_item and legacy_present
    )
    if len(state["items"]) != expected_item_count:
        raise RuntimeError("Post-write item count is not the expected split count.")
    if len(state["payments"]) != EXPECTED_PAYMENT_COUNT:
        raise RuntimeError("Post-write payment count is not exactly 35.")
    if state["scope"].get("budgetAmount") != int(EXPECTED_CONTRACT_AMOUNT):
        raise RuntimeError("Post-write Owner Scope budget is incorrect.")
    if state["paymentTotal"] != EXPECTED_PAYMENT_TOTAL:
        raise RuntimeError("Post-write Owner Scope total is incorrect.")
    if set(state["itemsBySource"]) != set(state["transactionsById"]):
        raise RuntimeError("Item source identities are incomplete or duplicated.")
    if len(set(state["itemsBySource"].values())) != EXPECTED_PAYMENT_COUNT:
        raise RuntimeError("Duplicate item IDs detected.")
    if len(state["paymentsBySource"]) != EXPECTED_PAYMENT_COUNT:
        raise RuntimeError("Payment source identities are incomplete or duplicated.")
    item_ids = set(state["itemsBySource"].values())
    payments_per_item = {item_id: 0 for item_id in item_ids}
    for item_id, item in state["items"]:
        if item_id == LEGACY_ITEM_ID and allow_legacy_item:
            continue
        if item.get("sourceLegacyTransactionId") not in state["transactionsById"]:
            raise RuntimeError(f"Item {item_id} has an unexpected source identity.")
        if item.get("category") not in OWNER_SCOPE_CATEGORIES:
            raise RuntimeError(f"Item {item_id} has an invalid category.")
        if item.get("budgetAmount") is not None:
            budget = Decimal(str(item["budgetAmount"]))
            if not budget.is_finite() or budget < 0:
                raise RuntimeError(f"Item {item_id} has an invalid individual budget.")
    for payment_id, payment in state["payments"]:
        item_id = payment.get("expenseItemId")
        if item_id not in item_ids:
            raise RuntimeError(f"Payment {payment_id} has an invalid expenseItemId.")
        if not payment.get("sourceLegacyTransactionId"):
            raise RuntimeError(f"Payment {payment_id} lacks a source identity.")
        payments_per_item[item_id] += 1
    if any(count != 1 for count in payments_per_item.values()):
        raise RuntimeError("Each Owner Scope item must have exactly one payment.")
    if legacy_present and any(
        payment.get("expenseItemId") == LEGACY_ITEM_ID
        for _payment_id, payment in state["payments"]
    ):
        raise RuntimeError("The combined item still has linked payments.")


def _encode_value(value):
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if value is None:
        return {"nullValue": None}
    if isinstance(value, str):
        return {"stringValue": value}
    raise TypeError(f"Unsupported Firestore REST write value: {type(value).__name__}")


def _encode_fields(fields):
    return {key: _encode_value(value) for key, value in fields.items()}


def main():
    parser = argparse.ArgumentParser(
        description="Split existing Owner Scope transactions into deterministic items."
    )
    parser.add_argument("--apply", action="store_true", help="Apply the REST-only split.")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "mannat-moon-construction-firebase-adminsdk-fbsvc-6881377704.json",
    )
    args = parser.parse_args()
    credentials = service_account.Credentials.from_service_account_file(
        str(args.credentials),
        scopes=[FIRESTORE_SCOPE],
    )
    result = migrate_split_items(
        FirestoreRest(AuthorizedSession(credentials)),
        apply=args.apply,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
