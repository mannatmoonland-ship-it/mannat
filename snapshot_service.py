import base64
import io
import json
import re
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SNAPSHOT_FORMAT_VERSION = 1
SNAPSHOT_PREFIX = "MannatMoon_Snapshot_"
SNAPSHOT_MEMBERS = {"metadata.json", "documents.jsonl"}
MAX_BATCH_WRITES = 400


class SnapshotError(Exception):
    pass


class RestoreFailure(SnapshotError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


class ReferencePath:
    def __init__(self, path):
        self.path = path


def _encode_value(value):
    if isinstance(value, datetime):
        return {
            "$snapshot_type": "timestamp",
            "iso": value.isoformat(),
            "nanosecond": getattr(value, "nanosecond", value.microsecond * 1000),
        }
    if isinstance(value, bytes):
        return {"$snapshot_type": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {
            "$snapshot_type": "map",
            "entries": [[str(key), _encode_value(item)] for key, item in value.items()],
        }
    if isinstance(value, (list, tuple)):
        return [_encode_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if value.__class__.__name__ == "GeoPoint" and hasattr(value, "latitude") and hasattr(value, "longitude"):
        return {
            "$snapshot_type": "geo_point",
            "latitude": value.latitude,
            "longitude": value.longitude,
        }
    if value.__class__.__name__ == "DocumentReference" and isinstance(getattr(value, "path", None), str):
        return {"$snapshot_type": "reference", "path": value.path}
    raise SnapshotError(f"Unsupported Firestore value type: {type(value).__name__}")


def _decode_value(value, db=None):
    if isinstance(value, list):
        return [_decode_value(item, db) for item in value]
    if not isinstance(value, dict):
        return value

    value_type = value.get("$snapshot_type")
    if value_type == "map":
        entries = value.get("entries")
        if not isinstance(entries, list):
            raise SnapshotError("Malformed map value in snapshot.")
        result = {}
        for entry in entries:
            if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str):
                raise SnapshotError("Malformed map entry in snapshot.")
            if entry[0] in result:
                raise SnapshotError("Duplicate map key in snapshot.")
            result[entry[0]] = _decode_value(entry[1], db)
        return result
    if value_type == "timestamp":
        iso_value = value.get("iso")
        nanos = value.get("nanosecond")
        if not isinstance(iso_value, str) or not isinstance(nanos, int) or not 0 <= nanos < 1_000_000_000:
            raise SnapshotError("Malformed timestamp in snapshot.")
        try:
            from google.api_core.datetime_helpers import DatetimeWithNanoseconds
        except ImportError:
            return datetime.fromisoformat(iso_value)
        try:
            return DatetimeWithNanoseconds.from_rfc3339(_with_nanos(iso_value, nanos))
        except ValueError as error:
            raise SnapshotError("Malformed Firestore timestamp in snapshot.") from error
    if value_type == "bytes":
        try:
            return base64.b64decode(value.get("base64", ""), validate=True)
        except (ValueError, TypeError) as error:
            raise SnapshotError("Malformed bytes value in snapshot.") from error
    if value_type == "geo_point":
        latitude = value.get("latitude")
        longitude = value.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            raise SnapshotError("Malformed GeoPoint in snapshot.")
        try:
            from google.cloud.firestore_v1 import GeoPoint
            return GeoPoint(latitude, longitude)
        except ImportError as error:
            raise SnapshotError("Firestore GeoPoint support is unavailable.") from error
    if value_type == "reference":
        path = value.get("path")
        if not _valid_document_path(path):
            raise SnapshotError("Malformed document reference in snapshot.")
        return db.document(path) if db is not None else ReferencePath(path)
    raise SnapshotError("Unknown or malformed tagged value in snapshot.")


def _with_nanos(iso_value, nanos):
    parsed = datetime.fromisoformat(iso_value)
    parsed = parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    base = parsed.strftime("%Y-%m-%dT%H:%M:%S")
    fraction = f"{nanos:09d}".rstrip("0")
    return f"{base}.{fraction}Z" if fraction else f"{base}Z"


def _valid_collection_path(path):
    return isinstance(path, str) and bool(path) and not path.startswith("/") and not path.endswith("/") and len(path.split("/")) % 2 == 1 and all(path.split("/"))


def _valid_document_path(path):
    return isinstance(path, str) and bool(path) and not path.startswith("/") and not path.endswith("/") and len(path.split("/")) % 2 == 0 and all(path.split("/"))


def _collection_full_path(collection_ref):
    parent = collection_ref.parent
    return f"{parent.path}/{collection_ref.id}" if parent is not None else collection_ref.id


def _walk_collection(collection_ref):
    for document_snapshot in collection_ref.stream():
        yield _collection_full_path(collection_ref), document_snapshot.id, document_snapshot.to_dict()
        for nested_collection in document_snapshot.reference.collections():
            yield from _walk_collection(nested_collection)


def iter_firestore_documents(db):
    for collection_ref in db.collections():
        yield from _walk_collection(collection_ref)


def create_snapshot(db, project_id, backup_directory):
    backup_directory = Path(backup_directory)
    backup_directory.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc)
    filename = f"{SNAPSHOT_PREFIX}{created_at:%Y-%m-%d_%H%M%S}.zip"
    destination = backup_directory / filename
    if destination.exists():
        destination = backup_directory / f"{SNAPSHOT_PREFIX}{created_at:%Y-%m-%d_%H%M%S}_{uuid.uuid4().hex[:8]}.zip"

    counts = Counter()
    total_documents = 0
    try:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            with archive.open("documents.jsonl", "w") as raw_stream:
                with io.TextIOWrapper(raw_stream, encoding="utf-8", newline="\n") as stream:
                    for collection_path, document_id, data in iter_firestore_documents(db):
                        if not _valid_collection_path(collection_path) or not document_id or "/" in document_id or not isinstance(data, dict):
                            raise SnapshotError("Firestore returned an invalid document path or data structure.")
                        record = {
                            "collection": collection_path,
                            "id": document_id,
                            "data": _encode_value(data),
                        }
                        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
                        stream.write("\n")
                        counts[collection_path] += 1
                        total_documents += 1

            metadata = {
                "format_version": SNAPSHOT_FORMAT_VERSION,
                "project_id": str(project_id),
                "created_at": created_at.isoformat(),
                "collections": [
                    {"path": name, "document_count": counts[name]}
                    for name in sorted(counts)
                ],
                "document_count": total_documents,
            }
            archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False))
        verification = verify_snapshot(destination)
        if not verification["verified"]:
            raise SnapshotError("Snapshot archive verification failed: " + "; ".join(verification["errors"]))
        return {"filename": destination.name, **metadata, "size": destination.stat().st_size, "verified": True}
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def verify_snapshot(snapshot_path):
    snapshot_path = Path(snapshot_path)
    errors = []
    try:
        with zipfile.ZipFile(snapshot_path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != SNAPSHOT_MEMBERS:
                raise SnapshotError("Snapshot ZIP must contain exactly metadata.json and documents.jsonl.")
            if archive.testzip() is not None:
                raise SnapshotError("Snapshot ZIP failed its CRC integrity check.")
            metadata = json.loads(archive.read("metadata.json").decode("utf-8"))
            if not isinstance(metadata, dict) or metadata.get("format_version") != SNAPSHOT_FORMAT_VERSION:
                raise SnapshotError("Unsupported or invalid snapshot metadata.")
            if not isinstance(metadata.get("project_id"), str) or not metadata["project_id"].strip():
                raise SnapshotError("Snapshot source project ID is missing.")
            datetime.fromisoformat(metadata.get("created_at", ""))
            collection_items = metadata.get("collections")
            if not isinstance(collection_items, list) or not isinstance(metadata.get("document_count"), int):
                raise SnapshotError("Snapshot collection metadata is invalid.")
            declared_counts = {}
            for item in collection_items:
                if not isinstance(item, dict) or not _valid_collection_path(item.get("path")) or not isinstance(item.get("document_count"), int) or item["document_count"] < 0:
                    raise SnapshotError("Snapshot collection entry is invalid.")
                if item["path"] in declared_counts:
                    raise SnapshotError("Snapshot contains duplicate collection metadata.")
                declared_counts[item["path"]] = item["document_count"]

            actual_counts = Counter()
            seen_documents = set()
            with archive.open("documents.jsonl", "r") as raw_stream:
                for line_number, raw_line in enumerate(raw_stream, 1):
                    try:
                        record = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise SnapshotError(f"Invalid document data at line {line_number}.") from error
                    collection_path = record.get("collection") if isinstance(record, dict) else None
                    document_id = record.get("id") if isinstance(record, dict) else None
                    if not _valid_collection_path(collection_path) or not isinstance(document_id, str) or not document_id or "/" in document_id:
                        raise SnapshotError(f"Invalid collection path or document ID at line {line_number}.")
                    key = (collection_path, document_id)
                    if key in seen_documents:
                        raise SnapshotError(f"Duplicate document ID at line {line_number}.")
                    seen_documents.add(key)
                    decoded = _decode_value(record.get("data"))
                    if not isinstance(decoded, dict):
                        raise SnapshotError(f"Document data is not a map at line {line_number}.")
                    actual_counts[collection_path] += 1
            if dict(actual_counts) != declared_counts or sum(actual_counts.values()) != metadata["document_count"]:
                raise SnapshotError("Snapshot document counts do not match metadata.")
            return {"verified": True, "errors": [], "metadata": metadata}
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, ValueError, SnapshotError) as error:
        errors.append(str(error))
    return {"verified": False, "errors": errors, "metadata": None}


def _read_records(snapshot_path, db):
    with zipfile.ZipFile(snapshot_path, "r") as archive:
        with archive.open("documents.jsonl", "r") as stream:
            for raw_line in stream:
                record = json.loads(raw_line.decode("utf-8"))
                yield record["collection"], record["id"], _decode_value(record["data"], db)


def preview_restore(snapshot_path, destination_db):
    verification = verify_snapshot(snapshot_path)
    if not verification["verified"]:
        raise SnapshotError("Snapshot verification failed: " + "; ".join(verification["errors"]))
    metadata = verification["metadata"]
    existing_documents = set()
    existing_count = 0
    for collection_path, document_id, _ in iter_firestore_documents(destination_db):
        existing_documents.add((collection_path, document_id))
        existing_count += 1
    source_keys = {
        (collection_path, document_id)
        for collection_path, document_id, _ in _read_records(snapshot_path, destination_db)
    }
    conflicts = sorted(source_keys & existing_documents)
    return {
        "source_project_id": metadata["project_id"],
        "created_at": metadata["created_at"],
        "collections": metadata["collections"],
        "document_count": metadata["document_count"],
        "destination_existing_document_count": existing_count,
        "conflict_count": len(conflicts),
        "conflicts": [f"{collection}/{document_id}" for collection, document_id in conflicts[:250]],
        "conflicts_truncated": len(conflicts) > 250,
        "_conflict_keys": [f"{collection}/{document_id}" for collection, document_id in conflicts],
    }


def restore_snapshot(snapshot_path, destination_db, allow_skip_conflicts=False, overwrite_conflicts=False, progress=None):
    if allow_skip_conflicts and overwrite_conflicts:
        raise SnapshotError("Choose either skip or overwrite for destination conflicts, not both.")
    preview = preview_restore(snapshot_path, destination_db)
    if preview["conflict_count"] and not allow_skip_conflicts and not overwrite_conflicts:
        raise SnapshotError("Destination conflicts exist; restoration was not started.")
    preview_conflicts = set(preview["_conflict_keys"])

    written_documents = []
    restored = Counter()
    skipped = Counter()
    overwritten = Counter()
    failed_collection = None
    batch = destination_db.batch()
    batch_size = 0
    batch_counts = Counter()
    batch_overwritten = Counter()
    batch_documents = []

    def commit_batch():
        nonlocal batch, batch_size, batch_counts, batch_overwritten
        if batch_size:
            batch.commit()
            for collection_path, count in batch_counts.items():
                restored[collection_path] += count - batch_overwritten[collection_path]
            overwritten.update(batch_overwritten)
            written_documents.extend(batch_documents)
            batch = destination_db.batch()
            batch_size = 0
            batch_counts = Counter()
            batch_overwritten = Counter()
            batch_documents.clear()

    try:
        for collection_path, document_id, data in _read_records(snapshot_path, destination_db):
            failed_collection = collection_path
            reference = destination_db.document(f"{collection_path}/{document_id}")
            if reference.get().exists:
                conflict_key = f"{collection_path}/{document_id}"
                if conflict_key not in preview_conflicts:
                    raise SnapshotError(f"A new destination conflict appeared after preview: {conflict_key}")
                if allow_skip_conflicts:
                    skipped[collection_path] += 1
                    continue
                if not overwrite_conflicts:
                    raise SnapshotError(f"A destination conflict appeared during restore: {collection_path}/{document_id}")
                batch_overwritten[collection_path] += 1
            batch.set(reference, data)
            batch_size += 1
            batch_counts[collection_path] += 1
            batch_documents.append((collection_path, document_id, data))
            if batch_size >= MAX_BATCH_WRITES:
                commit_batch()
                if progress:
                    progress(collection_path, restored[collection_path])
        commit_batch()
        for collection_path, document_id, expected_data in written_documents:
            actual = destination_db.document(f"{collection_path}/{document_id}").get().to_dict()
            if _encode_value(actual) != _encode_value(expected_data):
                raise SnapshotError(f"Restored document verification failed: {collection_path}/{document_id}")
        return {
            "verified": True,
            "collections_restored": dict(restored),
            "collections_overwritten": dict(overwritten),
            "collections_skipped": dict(skipped),
            "documents_restored": sum(restored.values()),
            "documents_skipped": sum(skipped.values()),
            "documents_overwritten": sum(overwritten.values()),
            "conflict_count": preview["conflict_count"],
            "source_project_id": preview["source_project_id"],
            "collections": preview["collections"],
        }
    except Exception as error:
        total_by_collection = {item["path"]: item["document_count"] for item in preview["collections"]}
        incomplete = {
            path: max(0, count - restored[path] - overwritten[path] - skipped[path])
            for path, count in total_by_collection.items()
            if count - restored[path] - overwritten[path] - skipped[path] > 0
        }
        report = {
            "verified": False,
            "failed_collection": failed_collection or "unknown",
            "collections_restored": dict(restored),
            "collections_overwritten": dict(overwritten),
            "collections_skipped": dict(skipped),
            "collections_incomplete": incomplete,
            "documents_restored": sum(restored.values()),
            "documents_overwritten": sum(overwritten.values()),
            "documents_skipped": sum(skipped.values()),
            "documents_incomplete": sum(incomplete.values()),
        }
        raise RestoreFailure(
            f"Restore failed in collection {report['failed_collection']} after writing "
            f"{sum(restored.values()) + sum(overwritten.values())} documents: {error}",
            report,
        ) from error


def is_snapshot_filename(filename):
    return bool(re.fullmatch(r"MannatMoon_Snapshot_[A-Za-z0-9_-]+\.zip", filename or ""))