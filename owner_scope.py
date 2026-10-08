from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
import uuid

from google.api_core.exceptions import AlreadyExists


OWNER_SCOPE_COLLECTION = "owner_scopes"
OWNER_SCOPE_DOCUMENT_ID = "owner-scope"
LEGACY_OWNER_ACCOUNT_COLLECTION = "trades"
LEGACY_OWNER_ACCOUNT_DOCUMENT_ID = "8f5d405c-c65b-4b0a-88bb-cbda4619f90b"
OWNER_SCOPE_CATEGORIES = (
    "labour",
    "material",
    "equipment_plant",
    "professional_technical",
    "government_statutory",
    "legal_documentation",
    "site_expense",
    "mixed",
    "other",
)
OWNER_SCOPE_PAYMENT_MODES = ("Cash", "Bank Transfer", "UPI", "Cheque", "Other")


class OwnerScopeConflictError(ValueError):
    status = 409


def _run_transaction(db, callback):
    from firebase_admin import firestore

    return firestore.transactional(callback)(db.transaction())


def _valid_document_id(value):
    if not isinstance(value, str) or not value or value in {".", ".."} or "/" in value:
        return False
    try:
        if len(value.encode("utf-8")) > 1500:
            return False
    except UnicodeEncodeError:
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in value)


def _matches_owner_scope_metadata(data):
    return (
        data.get("name") == "Owner Scope"
        and data.get("legacySource")
        == {
            "collection": LEGACY_OWNER_ACCOUNT_COLLECTION,
            "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        }
    )


def initialize_owner_scope(db):
    legacy_ref = db.collection(LEGACY_OWNER_ACCOUNT_COLLECTION).document(
        LEGACY_OWNER_ACCOUNT_DOCUMENT_ID
    )
    legacy_snapshot = legacy_ref.get()
    if not legacy_snapshot.exists:
        raise RuntimeError("The legacy Owner Account trade document was not found.")
    if (legacy_snapshot.to_dict() or {}).get("name") != "OWNERS ACCOUNT":
        raise RuntimeError("The legacy Owner Account document ID does not match.")

    scope_ref = db.collection(OWNER_SCOPE_COLLECTION).document(OWNER_SCOPE_DOCUMENT_ID)
    existing_scope = scope_ref.get()
    if existing_scope.exists:
        if not _matches_owner_scope_metadata(existing_scope.to_dict() or {}):
            raise RuntimeError(
                "The Owner Scope document exists but has an unexpected legacy reference."
            )
        return {"created": False, "document_id": OWNER_SCOPE_DOCUMENT_ID}

    now = datetime.now(timezone.utc)
    metadata = {
        "name": "Owner Scope",
        "legacySource": {
            "collection": LEGACY_OWNER_ACCOUNT_COLLECTION,
            "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        },
        "createdAt": now,
        "updatedAt": now,
    }

    try:
        scope_ref.create(metadata)
    except AlreadyExists:
        raced_scope = scope_ref.get()
        if not raced_scope.exists or not _matches_owner_scope_metadata(
            raced_scope.to_dict() or {}
        ):
            raise
        return {"created": False, "document_id": OWNER_SCOPE_DOCUMENT_ID}

    return {"created": True, "document_id": OWNER_SCOPE_DOCUMENT_ID}


def _owner_scope_references(db):
    scope_ref = db.collection(OWNER_SCOPE_COLLECTION).document(OWNER_SCOPE_DOCUMENT_ID)
    scope_snapshot = scope_ref.get()
    if not scope_snapshot.exists:
        raise RuntimeError("Owner Scope metadata has not been initialized.")

    metadata = scope_snapshot.to_dict() or {}
    legacy_source = metadata.get("legacySource")
    if (
        metadata.get("name") != "Owner Scope"
        or legacy_source
        != {
            "collection": LEGACY_OWNER_ACCOUNT_COLLECTION,
            "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        }
    ):
        raise RuntimeError("Owner Scope metadata has an unexpected legacy reference.")

    return scope_ref, metadata


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def get_owner_scope_data(db):
    scope_ref, metadata = _owner_scope_references(db)

    item_snapshots = list(scope_ref.collection("items").stream())
    items = []
    for snapshot in item_snapshots:
        item = snapshot.to_dict() or {}
        items.append(
            _json_value(
                {
                    "id": snapshot.id,
                    "name": item.get("name"),
                    "category": item.get("category"),
                    "sourceLegacyTradeId": item.get("sourceLegacyTradeId"),
                    "sourceLegacyTransactionId": item.get(
                        "sourceLegacyTransactionId"
                    ),
                    "remarks": item.get("remarks", ""),
                    "createdAt": item.get("createdAt"),
                    "updatedAt": item.get("updatedAt"),
                }
            )
        )
    items.sort(key=lambda item: str(item.get("name") or "").casefold())

    payment_snapshots = list(scope_ref.collection("payments").stream())
    payments = []
    for snapshot in payment_snapshots:
        payment = snapshot.to_dict() or {}
        payments.append(
            _json_value(
                {
                    "id": snapshot.id,
                    "expenseItemId": payment.get("expenseItemId"),
                    "sourceLegacyTransactionId": payment.get(
                        "sourceLegacyTransactionId"
                    ),
                    "sourceTransactionId": payment.get("sourceTransactionId"),
                    "date": payment.get("date"),
                    "amount": payment.get("amount", 0),
                    "paymentMode": payment.get("paymentMode", ""),
                    "remarks": payment.get("remarks", ""),
                }
            )
        )
    payments.sort(key=lambda payment: (str(payment.get("date") or ""), str(payment.get("id") or "")), reverse=True)

    return {
        "metadata": _json_value(
            {
                "id": scope_ref.id,
                "name": metadata.get("name"),
                "legacySource": metadata.get("legacySource"),
            }
        ),
        "items": items,
        "payments": payments,
    }


def _validated_item_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Owner Scope item data is required.")
    if set(payload) != {"name", "category", "remarks"}:
        raise ValueError("Provide only item name, category, and remarks.")

    name = payload["name"]
    category = payload["category"]
    remarks = payload["remarks"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Item name cannot be empty.")
    name = " ".join(name.split())
    if len(name) > 200:
        raise ValueError("Item name must be 200 characters or fewer.")
    if not isinstance(category, str) or category not in OWNER_SCOPE_CATEGORIES:
        raise ValueError("Choose one of the allowed Owner Scope categories.")
    if not isinstance(remarks, str):
        raise ValueError("Remarks must be text.")
    remarks = remarks.strip()
    if len(remarks) > 2000:
        raise ValueError("Remarks must be 2000 characters or fewer.")

    return {
        "name": name,
        "category": category,
        "remarks": remarks,
    }


def _validated_payment_payload(payload):
    if not isinstance(payload, dict) or not {"paymentDate", "amountPaid", "paymentMode"} <= set(payload):
        raise ValueError("Payment date, amount, and mode are required.")
    payment_date = payload["paymentDate"]
    if not isinstance(payment_date, str):
        raise ValueError("Payment date must be a valid date.")
    try:
        parsed_date = datetime.strptime(payment_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Payment date must be a valid date.") from exc
    if parsed_date.strftime("%Y-%m-%d") != payment_date:
        raise ValueError("Payment date must be a valid date.")

    amount = payload["amountPaid"]
    if isinstance(amount, bool):
        raise ValueError("Amount Paid must be a number greater than or equal to zero.")
    try:
        decimal_amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            "Amount Paid must be a number greater than or equal to zero."
        ) from exc
    amount_number = float(decimal_amount)
    if not decimal_amount.is_finite() or not math.isfinite(amount_number) or decimal_amount < 0:
        raise ValueError("Amount Paid must be a number greater than or equal to zero.")

    payment_mode = payload["paymentMode"]
    if (
        not isinstance(payment_mode, str)
        or not payment_mode.strip()
        or len(payment_mode.strip()) > 100
    ):
        raise ValueError("Choose a valid payment mode.")

    return {
        "date": payment_date,
        "amount": (
            int(decimal_amount)
            if decimal_amount == decimal_amount.to_integral_value()
            else float(decimal_amount)
        ),
        "paymentMode": payment_mode.strip(),
    }


def create_owner_scope_item(db, payload, item_id=None, payment_id=None):
    item_fields = {"name", "category", "remarks"}
    payment_fields = {"paymentDate", "amountPaid", "paymentMode"}
    if not isinstance(payload, dict) or set(payload) != item_fields | payment_fields:
        raise ValueError(
            "Provide item details and payment date, amount, and mode."
        )

    validated = _validated_item_payload(
        {key: payload[key] for key in item_fields}
    )
    payment = _validated_payment_payload(payload)
    scope_ref, _ = _owner_scope_references(db)
    item_id = item_id or str(uuid.uuid4())
    payment_id = payment_id or str(uuid.uuid4())
    if not _valid_document_id(item_id) or not _valid_document_id(payment_id):
        raise ValueError("Invalid Owner Scope item or payment ID.")
    item_ref = scope_ref.collection("items").document(item_id)
    payment_ref = scope_ref.collection("payments").document(payment_id)
    item_snapshot = item_ref.get()
    payment_snapshot = payment_ref.get()
    if item_snapshot.exists or payment_snapshot.exists:
        existing_item = item_snapshot.to_dict() or {}
        existing_payment = payment_snapshot.to_dict() or {}
        if (
            item_snapshot.exists
            and payment_snapshot.exists
            and all(existing_item.get(key) == value for key, value in validated.items())
            and all(existing_payment.get(key) == value for key, value in payment.items())
            and existing_payment.get("expenseItemId") == item_id
            and existing_payment.get("remarks") == validated["remarks"]
        ):
            return item_id
        raise OwnerScopeConflictError("This Owner Scope request ID has already been used.")
    now = datetime.now(timezone.utc)
    batch = db.batch()
    batch.create(item_ref, {**validated, "createdAt": now, "updatedAt": now})
    batch.create(
        payment_ref,
        {
            **payment,
            "expenseItemId": item_ref.id,
            "remarks": validated["remarks"],
            "createdAt": now,
            "updatedAt": now,
        },
    )
    try:
        batch.commit()
    except AlreadyExists:
        item_snapshot = item_ref.get()
        payment_snapshot = payment_ref.get()
        existing_item = item_snapshot.to_dict() or {}
        existing_payment = payment_snapshot.to_dict() or {}
        if (
            item_snapshot.exists
            and payment_snapshot.exists
            and all(existing_item.get(key) == value for key, value in validated.items())
            and all(existing_payment.get(key) == value for key, value in payment.items())
            and existing_payment.get("expenseItemId") == item_id
            and existing_payment.get("remarks") == validated["remarks"]
        ):
            return item_id
        raise OwnerScopeConflictError("This Owner Scope request ID has already been used.") from None
    return item_ref.id


def update_owner_scope_item(db, item_id, payload):
    if not _valid_document_id(item_id):
        raise ValueError("Invalid Owner Scope item ID.")
    item_fields = {"name", "category", "remarks"}
    payment_fields = {"paymentDate", "amountPaid", "paymentMode"}
    if not isinstance(payload, dict) or set(payload) not in (
        item_fields,
        item_fields | payment_fields,
    ):
        raise ValueError(
            "Provide item name, category, and remarks, with payment details for paid items."
        )
    validated = _validated_item_payload(
        {key: payload[key] for key in item_fields}
    )
    payment = (
        _validated_payment_payload(payload)
        if payment_fields.issubset(payload)
        else None
    )
    scope_ref, _ = _owner_scope_references(db)
    item_ref = scope_ref.collection("items").document(item_id)
    payments_collection = scope_ref.collection("payments")
    def mutate(transaction):
        item_snapshot = item_ref.get(transaction=transaction)
        if not item_snapshot.exists:
            return False

        linked_payments = list(
            transaction.get(
                payments_collection.where("expenseItemId", "==", item_id)
            )
        )
        if payment is not None and len(linked_payments) != 1:
            raise ValueError("This item must have exactly one linked payment to edit.")
        if payment is None and linked_payments:
            raise ValueError("Payment details are required to edit this paid item.")

        now = datetime.now(timezone.utc)
        if payment is not None:
            payment_ref = payments_collection.document(linked_payments[0].id)
            transaction.update(payment_ref, {**payment, "updatedAt": now})
        transaction.update(item_ref, {**validated, "updatedAt": now})
        return True

    return _run_transaction(db, mutate)


def delete_owner_scope_item(db, item_id):
    if not _valid_document_id(item_id):
        raise ValueError("Invalid Owner Scope item ID.")
    scope_ref, _ = _owner_scope_references(db)
    item_ref = scope_ref.collection("items").document(item_id)
    payments_collection = scope_ref.collection("payments")
    def mutate(transaction):
        item_snapshot = item_ref.get(transaction=transaction)
        if not item_snapshot.exists:
            return "not_found"

        linked_payments = list(
            transaction.get(
                payments_collection.where("expenseItemId", "==", item_id)
            )
        )
        if len(linked_payments) > 499:
            raise OwnerScopeConflictError(
                "This item has too many linked payments to delete safely."
            )
        for payment in linked_payments:
            transaction.delete(payments_collection.document(payment.id))
        transaction.delete(item_ref)
        return "deleted"

    return _run_transaction(db, mutate)
