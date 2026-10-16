"""Encrypted Firestore storage for per-user Daily Reports Google credentials."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken


COLLECTION_NAME = "daily_reports_drive_oauth_tokens"
DOCUMENT_SCHEMA_VERSION = 1
ENCRYPTION_KEY_VERSION = 1


def validate_encryption_key(encryption_key):
    if not isinstance(encryption_key, str) or not encryption_key.strip():
        raise TokenStoreConfigurationError(
            "GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY is not configured."
        )
    try:
        return Fernet(encryption_key.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as error:
        raise TokenStoreConfigurationError(
            "GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY is invalid."
        ) from error


class TokenStoreConfigurationError(RuntimeError):
    """The encrypted token store is not configured safely."""


class TokenRecordError(RuntimeError):
    """An encrypted token record is malformed or cannot be decrypted."""


class TokenStorageUnavailable(RuntimeError):
    """Firestore could not complete a token-storage operation."""


class TokenStorageConflict(RuntimeError):
    """A token changed concurrently too many times to safely refresh."""


@dataclass(frozen=True)
class StoredCredentials:
    serialized_credentials: str
    version: int


class EncryptedFirestoreTokenStore:
    def __init__(self, database, encryption_key, transactional):
        self._cipher = validate_encryption_key(encryption_key)
        if database is None:
            raise TokenStoreConfigurationError("Firestore token storage is unavailable.")
        if not callable(transactional):
            raise TokenStoreConfigurationError("Firestore transactions are unavailable.")
        self._database = database
        self._transactional = transactional

    @staticmethod
    def _user_document_id(user_id):
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("A signed-in user is required for Google Drive token storage.")
        return hashlib.sha256(user_id.encode("utf-8")).hexdigest()

    def _document(self, user_id):
        document_id = self._user_document_id(user_id)
        return self._database.collection(COLLECTION_NAME).document(document_id)

    def _encrypt(self, serialized_credentials):
        if not isinstance(serialized_credentials, str):
            raise TypeError("Serialized Google credentials must be text.")
        return self._cipher.encrypt(serialized_credentials.encode("utf-8")).decode("ascii")

    def load(self, user_id):
        try:
            snapshot = self._document(user_id).get()
        except Exception as error:
            raise TokenStorageUnavailable("Firestore token storage is unavailable.") from error
        if not snapshot.exists:
            return None
        record = snapshot.to_dict() or {}
        if not isinstance(record, dict):
            raise TokenRecordError("The Google Drive token record is invalid.")
        encrypted_credentials = record.get("encrypted_credentials")
        version = record.get("version")
        if (
            record.get("schema_version") != DOCUMENT_SCHEMA_VERSION
            or record.get("key_version") != ENCRYPTION_KEY_VERSION
            or not isinstance(encrypted_credentials, str)
            or not encrypted_credentials
            or isinstance(version, bool)
            or not isinstance(version, int)
            or version < 1
        ):
            raise TokenRecordError("The Google Drive token record is invalid.")
        try:
            serialized = self._cipher.decrypt(encrypted_credentials.encode("ascii"))
            return StoredCredentials(serialized.decode("utf-8"), version)
        except (UnicodeEncodeError, UnicodeDecodeError, InvalidToken) as error:
            raise TokenRecordError("The Google Drive token record cannot be decrypted.") from error

    def save(self, user_id, serialized_credentials):
        reference = self._document(user_id)
        encrypted_credentials = self._encrypt(serialized_credentials)

        def write(transaction):
            snapshot = next(transaction.get(reference))
            if snapshot.exists:
                current = snapshot.to_dict()
                if not isinstance(current, dict):
                    raise TokenRecordError("The Google Drive token record is invalid.")
                current_version = current.get("version")
                if (
                    isinstance(current_version, bool)
                    or not isinstance(current_version, int)
                    or current_version < 1
                ):
                    raise TokenRecordError(
                        "The Google Drive token record version is invalid."
                    )
            else:
                current_version = 0
            transaction.set(
                reference,
                self._new_record(encrypted_credentials, current_version + 1),
            )

        self._run_transaction(write)

    def save_if_version(self, user_id, serialized_credentials, expected_version):
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise ValueError("A valid Google Drive token version is required.")
        reference = self._document(user_id)
        encrypted_credentials = self._encrypt(serialized_credentials)

        def write_if_unchanged(transaction):
            snapshot = next(transaction.get(reference))
            if not snapshot.exists:
                return False
            current = snapshot.to_dict()
            if not isinstance(current, dict):
                raise TokenRecordError("The Google Drive token record is invalid.")
            current_version = current.get("version")
            if (
                isinstance(current_version, bool)
                or not isinstance(current_version, int)
                or current_version != expected_version
            ):
                return False
            transaction.set(
                reference,
                self._new_record(encrypted_credentials, expected_version + 1),
            )
            return True

        return self._run_transaction(write_if_unchanged)

    def _run_transaction(self, operation):
        try:
            transaction = self._database.transaction()
            return self._transactional(operation)(transaction)
        except TokenRecordError:
            raise
        except Exception as error:
            raise TokenStorageUnavailable(
                "Firestore token storage is unavailable."
            ) from error

    @staticmethod
    def _new_record(encrypted_credentials, version):
        return {
            "encrypted_credentials": encrypted_credentials,
            "schema_version": DOCUMENT_SCHEMA_VERSION,
            "key_version": ENCRYPTION_KEY_VERSION,
            "version": version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
