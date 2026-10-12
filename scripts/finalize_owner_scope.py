import argparse
import json
import sys
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
    OWNER_SCOPE_COLLECTION,
    OWNER_SCOPE_DOCUMENT_ID,
)


PROJECT_ID = "mannat-moon-construction"
OWNER_ACCOUNT_ITEM_ID = "legacy-owners-account"
EXPECTED_CONTRACT_AMOUNT = Decimal("10000000")
EXPECTED_PAYMENT_COUNT = 35
EXPECTED_PAYMENT_TOTAL = Decimal("5745171")
RELEVANT_PAYMENT_FIELDS = ("amount", "date", "paymentMode", "remarks")
FIRESTORE_SCOPE = "https://www.googleapis.com/auth/datastore"


def _decode_value(value):
    for key, converter in (
        ("stringValue", lambda item: item),
        ("integerValue", int),
        ("doubleValue", float),
        ("booleanValue", lambda item: item),
        ("nullValue", lambda _item: None),
        ("timestampValue", lambda item: item),
        ("referenceValue", lambda item: item),
    ):
        if key in value:
            return converter(value[key])
    if "mapValue" in value:
        return _decode_fields(value["mapValue"].get("fields", {}))
    if "arrayValue" in value:
        return [_decode_value(item) for item in value["arrayValue"].get("values", [])]
    raise RuntimeError(f"Unsupported Firestore REST value type: {tuple(value)}")


def _decode_fields(fields):
    return {key: _decode_value(value) for key, value in fields.items()}


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


class FirestoreRest:
    def __init__(self, session, project_id=PROJECT_ID):
        self.session = session
        self.base_url = (
            "https://firestore.googleapis.com/v1/projects/"
            f"{quote(project_id, safe='')}/databases/(default)/documents"
        )

    def get_document(self, document_path):
        response = self.session.get(
            f"{self.base_url}/{document_path}",
            timeout=30,
        )
        response.raise_for_status()
        document = response.json()
        return document.get("updateTime"), _decode_fields(document.get("fields", {}))

    def list_documents(self, collection_path):
        documents = []
        page_token = None
        while True:
            params = {"pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            response = self.session.get(
                f"{self.base_url}/{collection_path}",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            for document in payload.get("documents", []):
                document_id = document["name"].rsplit("/", 1)[1]
                documents.append(
                    (document_id, _decode_fields(document.get("fields", {})))
                )
            page_token = payload.get("nextPageToken")
            if not page_token:
                return documents

    def patch_document(self, document_path, fields):
        response = self.session.patch(
            f"{self.base_url}/{document_path}",
            params=[("updateMask.fieldPaths", key) for key in fields],
            json={"fields": _encode_fields(fields)},
            timeout=30,
        )
        response.raise_for_status()

    def create_document(self, collection_path, document_id, fields):
        response = self.session.post(
            f"{self.base_url}/{collection_path}",
            params={"documentId": document_id},
            json={"fields": _encode_fields(fields)},
            timeout=30,
        )
        response.raise_for_status()


def _source_data(rest):
    source_path = (
        f"{LEGACY_OWNER_ACCOUNT_COLLECTION}/{LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}"
    )
    update_time, source = rest.get_document(source_path)
    if source.get("name") != "OWNERS ACCOUNT":
        raise RuntimeError("Source trade name differs from OWNERS ACCOUNT.")

    contracts = source.get("contractItems")
    if not isinstance(contracts, list) or len(contracts) != 1:
        raise RuntimeError("Source contract item count is not exactly one.")
    contract = contracts[0]
    if Decimal(str(contract.get("amount"))) != EXPECTED_CONTRACT_AMOUNT:
        raise RuntimeError("Source contract amount differs from 10000000.")

    transactions = source.get("paymentTransactions")
    if not isinstance(transactions, list) or len(transactions) != EXPECTED_PAYMENT_COUNT:
        raise RuntimeError("Source payment transaction count is not exactly 35.")
    transaction_ids = [transaction.get("id") for transaction in transactions]
    if (
        any(not transaction_id for transaction_id in transaction_ids)
        or len(set(transaction_ids)) != EXPECTED_PAYMENT_COUNT
    ):
        raise RuntimeError("Source payment transaction IDs are missing or duplicated.")

    total = sum(
        (Decimal(str(transaction.get("amount"))) for transaction in transactions),
        Decimal(0),
    )
    if total != EXPECTED_PAYMENT_TOTAL:
        raise RuntimeError("Source payment total differs from 5745171.")
    return update_time, source, contract, transactions


def _read_destination(rest, transactions):
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

    item_path = f"{scope_path}/items"
    items = rest.list_documents(item_path)
    payment_path = f"{scope_path}/payments"
    payments = rest.list_documents(payment_path)
    if len(items) != 1 or items[0][0] != OWNER_ACCOUNT_ITEM_ID:
        raise RuntimeError("Owner Scope must contain only legacy-owners-account.")
    if len(payments) != EXPECTED_PAYMENT_COUNT:
        raise RuntimeError("Owner Scope must contain exactly 35 payment documents.")

    item_id, item = items[0]
    if item.get("name") != "Owners Account" or item.get("category") not in {
        "mixed",
        "Mixed",
    }:
        raise RuntimeError("Existing Owner Scope item has unexpected name or category.")
    if item.get("budgetAmount") not in (None, 10000000, 10000000.0):
        raise RuntimeError("Existing Owner Scope item budget is unexpectedly populated.")
    if item.get("sourceLegacyTradeId") not in (
        None,
        LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    ):
        raise RuntimeError("Existing Owner Scope item has an unexpected source marker.")

    source_by_id = {transaction["id"]: transaction for transaction in transactions}
    seen_source_ids = set()
    payment_total = Decimal(0)
    for payment_id, payment in payments:
        source_id = payment.get("sourceLegacyTransactionId")
        if source_id is None:
            source_id = payment.get("sourceTransactionId")
        if not source_id or source_id not in source_by_id:
            raise RuntimeError(
                f"Owner Scope payment {payment_id} does not match a source transaction."
            )
        if source_id in seen_source_ids:
            raise RuntimeError(f"Duplicate Owner Scope payment for {source_id}.")
        seen_source_ids.add(source_id)
        source = source_by_id[source_id]
        if payment.get("sourceLegacyTransactionId") not in (None, source_id):
            raise RuntimeError(f"Payment {payment_id} has a conflicting legacy marker.")
        if payment.get("sourceTransactionId") not in (None, source_id):
            raise RuntimeError(f"Payment {payment_id} has a conflicting source ID.")
        if payment.get("expenseItemId") != item_id:
            raise RuntimeError(f"Payment {payment_id} points to an unexpected item.")
        for field in RELEVANT_PAYMENT_FIELDS:
            if payment.get(field) != source.get(field):
                raise RuntimeError(
                    f"Payment {payment_id} field {field!r} differs from its source."
                )
        payment_total += Decimal(str(payment.get("amount")))

    if seen_source_ids != set(source_by_id):
        raise RuntimeError("Owner Scope payments do not cover all source transactions.")
    if payment_total != EXPECTED_PAYMENT_TOTAL:
        raise RuntimeError("Owner Scope payment total differs from 5745171.")

    return scope_time, item_id, item, payments, payment_total


def _ensure_document_matches(rest, path, expected_fields):
    _update_time, document = rest.get_document(path)
    for key, value in expected_fields.items():
        if document.get(key) != value:
            raise RuntimeError(
                f"Target {path} changed before updating field {key!r}; stopped."
            )
    return document


def _patch_with_progress(rest, path, fields, completed_writes):
    try:
        rest.patch_document(path, fields)
    except RequestException as error:
        completed = ", ".join(completed_writes) or "none"
        raise RuntimeError(
            f"REST patch failed for {path}. Previously confirmed patches: {completed}. "
            "Re-read the target before attempting to resume."
        ) from error


def finalize_owner_scope(rest):
    source_time, _source, contract, transactions = _source_data(rest)
    _scope_time, item_id, item, payments, payment_total = _read_destination(
        rest, transactions
    )

    item_updates = {}
    if item.get("budgetAmount") is None:
        item_updates["budgetAmount"] = int(EXPECTED_CONTRACT_AMOUNT)
    if item.get("sourceLegacyTradeId") is None:
        item_updates["sourceLegacyTradeId"] = LEGACY_OWNER_ACCOUNT_DOCUMENT_ID

    # Re-read the source immediately before the first metadata update.
    latest_source_time, _latest_source, latest_contract, latest_transactions = (
        _source_data(rest)
    )
    if (
        latest_source_time != source_time
        or latest_contract != contract
        or latest_transactions != transactions
    ):
        raise RuntimeError("Source changed during preflight; no writes were started.")

    writes = []
    item_path = (
        f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/items/{item_id}"
    )
    if item_updates:
        _ensure_document_matches(
            rest,
            item_path,
            {
                "name": item.get("name"),
                "category": item.get("category"),
                "budgetAmount": item.get("budgetAmount"),
                "sourceLegacyTradeId": item.get("sourceLegacyTradeId"),
            },
        )
        _patch_with_progress(rest, item_path, item_updates, writes)
        writes.append(item_path)

    source_by_id = {transaction["id"]: transaction for transaction in transactions}
    for payment_id, observed_payment in payments:
        source_id = observed_payment.get("sourceLegacyTransactionId")
        if source_id is None:
            source_id = observed_payment.get("sourceTransactionId")
        if observed_payment.get("sourceLegacyTransactionId") == source_id:
            continue

        payment_path = (
            f"{OWNER_SCOPE_COLLECTION}/{OWNER_SCOPE_DOCUMENT_ID}/payments/"
            f"{payment_id}"
        )
        _ensure_document_matches(
            rest,
            payment_path,
            {
                "sourceTransactionId": observed_payment.get("sourceTransactionId"),
                "sourceLegacyTransactionId": observed_payment.get(
                    "sourceLegacyTransactionId"
                ),
                "expenseItemId": item_id,
                **{
                    field: source_by_id[source_id].get(field)
                    for field in RELEVANT_PAYMENT_FIELDS
                },
            },
        )
        _patch_with_progress(
            rest,
            payment_path,
            {"sourceLegacyTransactionId": source_id},
            writes,
        )
        writes.append(payment_path)

    _verified_source_time, _verified_source, _contract, verified_transactions = (
        _source_data(rest)
    )
    _verified_scope_time, verified_item_id, verified_item, verified_payments, total = (
        _read_destination(rest, verified_transactions)
    )
    if verified_item.get("budgetAmount") != int(EXPECTED_CONTRACT_AMOUNT):
        raise RuntimeError("Final Owner Scope budget verification failed.")
    if verified_item.get("sourceLegacyTradeId") != LEGACY_OWNER_ACCOUNT_DOCUMENT_ID:
        raise RuntimeError("Final Owner Scope item marker verification failed.")
    if any(
        payment.get("sourceLegacyTransactionId") is None
        for _payment_id, payment in verified_payments
    ):
        raise RuntimeError("Some Owner Scope payments still lack source markers.")

    return {
        "alreadyFinalized": not writes,
        "itemCount": 1,
        "itemId": verified_item_id,
        "paymentCount": len(verified_payments),
        "sourceTotal": int(EXPECTED_PAYMENT_TOTAL),
        "ownerScopeTotal": int(total),
        "remaining": int(EXPECTED_CONTRACT_AMOUNT - total),
        "markersAdded": sum(
            1
            for path in writes
            if "/payments/" in path
        ),
        "writes": writes,
        "duplicates": 0,
        "missingPayments": 0,
        "financialMismatches": 0,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Finalize existing Owner Scope metadata using Firestore REST only."
    )
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
    result = finalize_owner_scope(FirestoreRest(AuthorizedSession(credentials)))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
