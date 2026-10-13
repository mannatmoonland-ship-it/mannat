from copy import deepcopy
from datetime import datetime, timezone

import pytest
from google.api_core.exceptions import AlreadyExists

from owner_scope import (
    LEGACY_OWNER_ACCOUNT_COLLECTION,
    LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    OWNER_SCOPE_COLLECTION,
    OWNER_SCOPE_DOCUMENT_ID,
    initialize_owner_scope,
)


class FakeSnapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return deepcopy(self._data)


class FakeDocumentReference:
    def __init__(self, database, collection_name, document_id):
        self.database = database
        self.collection_name = collection_name
        self.document_id = document_id

    def get(self):
        data = self.database.documents.get(
            (self.collection_name, self.document_id)
        )
        return FakeSnapshot(self.document_id, data)

    def create(self, data):
        key = (self.collection_name, self.document_id)
        if key in self.database.documents:
            raise AlreadyExists("Document already exists.")
        self.database.documents[key] = deepcopy(data)
        self.database.writes.append(key)


class FakeCollectionReference:
    def __init__(self, database, collection_name):
        self.database = database
        self.collection_name = collection_name

    def document(self, document_id):
        return FakeDocumentReference(
            self.database, self.collection_name, document_id
        )


class FakeFirestore:
    def __init__(self, documents):
        self.documents = deepcopy(documents)
        self.writes = []

    def collection(self, collection_name):
        return FakeCollectionReference(self, collection_name)


@pytest.fixture
def legacy_data():
    return {
        "name": "OWNERS ACCOUNT",
        "contractItems": [{"id": "cap-1", "amount": 10000000}],
        "workerCategories": [{"id": "worker-1", "name": "Helper"}],
        "paymentTransactions": [
            {"id": "payment-1", "amount": 1250, "remarks": "Mixed expense"},
            {"id": "payment-2", "amount": 875, "remarks": "Other expense"},
        ],
    }


def test_initialization_creates_only_additive_owner_scope_metadata(legacy_data):
    database = FakeFirestore(
        {
            (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID): legacy_data
        }
    )
    legacy_before = deepcopy(
        database.documents[
            (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID)
        ]
    )

    result = initialize_owner_scope(database)

    metadata = database.documents[(OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID)]
    assert result == {"created": True, "document_id": OWNER_SCOPE_DOCUMENT_ID}
    assert metadata["name"] == "Owner Scope"
    assert metadata["legacySource"] == {
        "collection": "trades",
        "documentId": LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    }
    assert isinstance(metadata["createdAt"], datetime)
    assert isinstance(metadata["updatedAt"], datetime)
    assert database.documents[
        (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID)
    ] == legacy_before
    assert database.writes == [(OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID)]
    assert not any(
        key[0] == OWNER_SCOPE_COLLECTION and key[1].startswith(f"{OWNER_SCOPE_DOCUMENT_ID}/")
        for key in database.documents
    )


def test_initialization_is_idempotent_and_does_not_update_existing_metadata(legacy_data):
    database = FakeFirestore(
        {
            (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID): legacy_data
        }
    )
    first_result = initialize_owner_scope(database)
    metadata_before_second_run = deepcopy(
        database.documents[(OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID)]
    )

    second_result = initialize_owner_scope(database)

    assert first_result["created"] is True
    assert second_result == {"created": False, "document_id": OWNER_SCOPE_DOCUMENT_ID}
    assert database.documents[(OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID)] == metadata_before_second_run
    assert database.writes == [(OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID)]


def test_initialization_rejects_wrong_or_missing_legacy_source_without_writes(legacy_data):
    database = FakeFirestore(
        {
            (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID): {
                **legacy_data,
                "name": "Different trade",
            }
        }
    )

    with pytest.raises(RuntimeError, match="document ID does not match"):
        initialize_owner_scope(database)

    assert database.writes == []
    assert (OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID) not in database.documents


def test_existing_owner_scope_with_different_legacy_link_is_not_modified(legacy_data):
    existing_metadata = {
        "name": "Owner Scope",
        "legacySource": {"collection": "trades", "documentId": "wrong-id"},
    }
    database = FakeFirestore(
        {
            (LEGACY_OWNER_ACCOUNT_COLLECTION, LEGACY_OWNER_ACCOUNT_DOCUMENT_ID): legacy_data,
            (OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID): existing_metadata,
        }
    )
    metadata_before = deepcopy(
        database.documents[(OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID)]
    )

    with pytest.raises(RuntimeError, match="unexpected legacy reference"):
        initialize_owner_scope(database)

    assert database.documents[(OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID)] == metadata_before
    assert database.writes == []
