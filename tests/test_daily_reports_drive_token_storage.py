import copy

import pytest
from cryptography.fernet import Fernet

from daily_reports_drive_tokens import (
    COLLECTION_NAME,
    EncryptedFirestoreTokenStore,
    TokenRecordError,
    TokenStorageUnavailable,
    TokenStoreConfigurationError,
)


def run_transactional(callback):
    return lambda transaction: callback(transaction)


class FakeSnapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self.exists = data is not None
        self._data = copy.deepcopy(data)

    def to_dict(self):
        return copy.deepcopy(self._data)


class FakeDocument:
    def __init__(self, collection, document_id):
        self.collection = collection
        self.id = document_id

    def get(self):
        if self.collection.fail_reads:
            raise OSError("private database details")
        return FakeSnapshot(self.id, self.collection.documents.get(self.id))


class FakeCollection:
    def __init__(self):
        self.documents = {}
        self.fail_reads = False

    def document(self, document_id):
        return FakeDocument(self, document_id)


class FakeTransaction:
    def get(self, reference):
        return iter([reference.get()])

    def set(self, reference, payload):
        reference.collection.documents[reference.id] = copy.deepcopy(payload)


class FakeDatabase:
    def __init__(self):
        self.token_collection = FakeCollection()
        self.fail_transactions = False

    def collection(self, collection_name):
        assert collection_name == COLLECTION_NAME
        return self.token_collection

    def transaction(self):
        if self.fail_transactions:
            raise OSError("private database transaction details")
        return FakeTransaction()


@pytest.fixture
def token_store():
    database = FakeDatabase()
    key = Fernet.generate_key().decode("ascii")
    return database, key, EncryptedFirestoreTokenStore(
        database,
        key,
        run_transactional,
    )


def test_credentials_round_trip_are_encrypted_and_isolated_by_user(token_store):
    database, _key, store = token_store
    serialized = '{"token":"access-secret","refresh_token":"refresh-secret"}'

    store.save("user-one", serialized)
    first = store.load("user-one")

    assert first.serialized_credentials == serialized
    assert first.version == 1
    stored_record = next(iter(database.token_collection.documents.values()))
    assert "access-secret" not in str(stored_record)
    assert "refresh-secret" not in str(stored_record)
    assert set(stored_record) == {
        "encrypted_credentials",
        "schema_version",
        "key_version",
        "version",
        "updated_at",
    }
    assert store.load("user-two") is None


@pytest.mark.parametrize("key", ["", "not-a-fernet-key", "ключ"])
def test_missing_or_invalid_encryption_key_fails_closed(token_store, key):
    database, _key, _store = token_store

    with pytest.raises(TokenStoreConfigurationError):
        EncryptedFirestoreTokenStore(database, key, run_transactional)


def test_tampered_ciphertext_is_rejected(token_store):
    database, _key, store = token_store
    store.save("user-one", '{"token":"private"}')
    document_id, record = next(iter(database.token_collection.documents.items()))
    record["encrypted_credentials"] = record["encrypted_credentials"][:-2] + "xx"
    database.token_collection.documents[document_id] = record

    with pytest.raises(TokenRecordError):
        store.load("user-one")


def test_wrong_key_cannot_decrypt_a_saved_record(token_store):
    database, _key, store = token_store
    store.save("user-one", '{"token":"private"}')
    other_key = Fernet.generate_key().decode("ascii")
    other_store = EncryptedFirestoreTokenStore(
        database,
        other_key,
        run_transactional,
    )

    with pytest.raises(TokenRecordError):
        other_store.load("user-one")


def test_compare_and_set_prevents_a_refresh_from_overwriting_newer_tokens(token_store):
    _database, _key, store = token_store
    store.save("user-one", '{"token":"original"}')
    original = store.load("user-one")
    store.save("user-one", '{"token":"newer"}')

    saved = store.save_if_version(
        "user-one",
        '{"token":"stale-refresh"}',
        original.version,
    )

    assert saved is False
    current = store.load("user-one")
    assert current.serialized_credentials == '{"token":"newer"}'
    assert current.version == 2


def test_firestore_read_failure_is_reported_without_database_details(token_store):
    database, _key, store = token_store
    database.token_collection.fail_reads = True

    with pytest.raises(TokenStorageUnavailable) as error:
        store.load("user-one")

    assert "private database details" not in str(error.value)


def test_firestore_write_failure_is_reported_without_database_details(token_store):
    database, _key, store = token_store
    database.fail_transactions = True

    with pytest.raises(TokenStorageUnavailable) as error:
        store.save("user-one", '{"token":"private"}')

    assert "private database transaction details" not in str(error.value)


def test_firestore_refresh_persistence_failure_is_not_reported_as_saved(token_store):
    database, _key, store = token_store
    store.save("user-one", '{"token":"before-refresh"}')
    current = store.load("user-one")
    database.fail_transactions = True

    with pytest.raises(TokenStorageUnavailable):
        store.save_if_version(
            "user-one",
            '{"token":"after-refresh"}',
            current.version,
        )

    database.fail_transactions = False
    assert store.load("user-one").serialized_credentials == '{"token":"before-refresh"}'
