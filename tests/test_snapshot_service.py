import copy
from datetime import datetime, timezone

import pytest

from snapshot_service import (
    SnapshotError,
    create_snapshot,
    is_snapshot_filename,
    preview_restore,
    restore_snapshot,
    verify_snapshot,
)


class FakeSnapshot:
    def __init__(self, reference, data):
        self.reference = reference
        self.id = reference.id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class FakeReference:
    def __init__(self, database, path):
        self.database = database
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    def get(self):
        return FakeSnapshot(self, self.database.documents.get(self.path))

    def collections(self):
        prefix = self.path + "/"
        names = {
            path[len(prefix):].split("/", 1)[0]
            for path in self.database.documents
            if path.startswith(prefix) and "/" in path[len(prefix):]
        }
        return [FakeCollection(self.database, f"{self.path}/{name}") for name in sorted(names)]


class DocumentReference(FakeReference):
    pass


class FakeCollection:
    def __init__(self, database, path):
        self.database = database
        self._segments = path.split("/")
        self.id = self._segments[-1]
        parent_path = "/".join(self._segments[:-1])
        self.parent = database.document(parent_path) if parent_path else None

    def stream(self):
        prefix = "/".join(self._segments) + "/"
        for path, data in self.database.documents.items():
            if path.startswith(prefix) and "/" not in path[len(prefix):]:
                yield FakeSnapshot(FakeReference(self.database, path), data)


class FakeBatch:
    def __init__(self, database):
        self.database = database
        self.writes = []

    def set(self, reference, data):
        self.writes.append((reference.path, data))

    def commit(self):
        for path, data in self.writes:
            self.database.documents[path] = data
        self.writes.clear()


class FailingBatch(FakeBatch):
    def commit(self):
        self.database.commit_count += 1
        if self.database.commit_count == 2:
            raise RuntimeError("simulated interruption")
        super().commit()


class FakeFirestore:
    def __init__(self, documents=None):
        self.documents = copy.deepcopy(documents or {})

    def collections(self):
        paths = {path.split("/", 1)[0] for path in self.documents}
        return [FakeCollection(self, path) for path in sorted(paths)]

    def document(self, path):
        return DocumentReference(self, path)

    def batch(self):
        return FakeBatch(self)


class InterruptedFirestore(FakeFirestore):
    def __init__(self, documents=None):
        super().__init__(documents)
        self.commit_count = 0

    def batch(self):
        return FailingBatch(self)


@pytest.fixture
def sample_database():
    return FakeFirestore({
        "trades/trade-original-id": {
            "name": "Bar Bender",
            "active": True,
            "rate": 1250.5,
            "workers": [{"name": "Mason", "count": 4}],
            "createdAt": datetime(2026, 9, 27, 12, 30, tzinfo=timezone.utc),
            "attachment": b"binary-data",
        },
        "daily_records/report-original-id": {"date": "2026-09-27", "totalWorkers": 0},
    })


def test_snapshot_verifies_and_restores_document_ids_and_types(tmp_path, sample_database):
    assert all(not hasattr(collection, "path") for collection in sample_database.collections())
    source_before = copy.deepcopy(sample_database.documents)
    created = create_snapshot(sample_database, "source-project", tmp_path)
    result = verify_snapshot(tmp_path / created["filename"])
    destination = FakeFirestore()

    restored = restore_snapshot(tmp_path / created["filename"], destination)

    assert result["verified"] is True
    assert result["metadata"]["document_count"] == 2
    assert created["collections"] == [
        {"path": "daily_records", "document_count": 1},
        {"path": "trades", "document_count": 1},
    ]
    assert restored["verified"] is True
    assert set(destination.documents) == set(sample_database.documents)
    assert destination.documents["trades/trade-original-id"] == sample_database.documents["trades/trade-original-id"]
    assert isinstance(destination.documents["trades/trade-original-id"]["createdAt"], datetime)
    assert isinstance(destination.documents["trades/trade-original-id"]["attachment"], bytes)
    assert destination.documents["trades/trade-original-id"]["active"] is True
    assert sample_database.documents == source_before


def test_snapshot_includes_nested_subcollections(tmp_path):
    source = FakeFirestore({
        "drawings/drawing-1": {"name": "Plan"},
        "drawings/drawing-1/revisions/revision-1": {"version": 2},
    })
    created = create_snapshot(source, "source-project", tmp_path)
    destination = FakeFirestore()

    result = restore_snapshot(tmp_path / created["filename"], destination)

    assert result["verified"] is True
    assert destination.documents == source.documents
    assert created["collections"] == [
        {"path": "drawings", "document_count": 1},
        {"path": "drawings/drawing-1/revisions", "document_count": 1},
    ]


def test_snapshot_preserves_firestore_special_types_and_tag_like_user_maps(tmp_path):
    from google.api_core.datetime_helpers import DatetimeWithNanoseconds
    from google.cloud.firestore_v1 import GeoPoint

    source = FakeFirestore({"records/record-1": {"name": "Linked"}})
    source.documents["records/record-1"].update({
        "createdAt": DatetimeWithNanoseconds.from_rfc3339("2026-09-27T12:30:00.123456789Z"),
        "location": GeoPoint(31.5, 74.3),
        "related": source.document("records/record-1"),
        "userMap": {"$snapshot_type": "bytes", "base64": "not-a-tag"},
    })
    created = create_snapshot(source, "source-project", tmp_path)
    destination = FakeFirestore()

    restore_snapshot(tmp_path / created["filename"], destination)
    restored = destination.documents["records/record-1"]

    assert restored["createdAt"].nanosecond == 123456789
    assert restored["location"].latitude == 31.5
    assert restored["location"].longitude == 74.3
    assert restored["related"].path == "records/record-1"
    assert restored["userMap"] == {"$snapshot_type": "bytes", "base64": "not-a-tag"}


def test_restore_detects_and_does_not_overwrite_destination_conflicts(tmp_path):
    source = FakeFirestore({"trades/trade-1": {"name": "Source"}, "trades/trade-2": {"name": "New"}})
    created = create_snapshot(source, "source-project", tmp_path)
    destination = FakeFirestore({"trades/trade-1": {"name": "Destination"}})

    preview = preview_restore(tmp_path / created["filename"], destination)
    assert preview["conflict_count"] == 1
    assert preview["conflicts"] == ["trades/trade-1"]
    with pytest.raises(SnapshotError, match="conflicts exist"):
        restore_snapshot(tmp_path / created["filename"], destination)

    result = restore_snapshot(tmp_path / created["filename"], destination, allow_skip_conflicts=True)

    assert destination.documents["trades/trade-1"] == {"name": "Destination"}
    assert destination.documents["trades/trade-2"] == {"name": "New"}
    assert result["documents_restored"] == 1
    assert result["documents_skipped"] == 1


def test_restore_overwrites_conflicts_only_when_explicitly_requested(tmp_path):
    source = FakeFirestore({"trades/trade-1": {"name": "Snapshot"}})
    created = create_snapshot(source, "source-project", tmp_path)
    destination = FakeFirestore({"trades/trade-1": {"name": "Existing"}})

    result = restore_snapshot(tmp_path / created["filename"], destination, overwrite_conflicts=True)

    assert destination.documents["trades/trade-1"] == {"name": "Snapshot"}
    assert result["documents_overwritten"] == 1


def test_interrupted_restore_reports_committed_and_incomplete_documents(tmp_path):
    source = FakeFirestore({f"trades/trade-{index}": {"index": index} for index in range(401)})
    created = create_snapshot(source, "source-project", tmp_path)
    destination = InterruptedFirestore()

    with pytest.raises(SnapshotError) as error:
        restore_snapshot(tmp_path / created["filename"], destination)

    assert error.value.report["documents_restored"] == 400
    assert error.value.report["documents_incomplete"] == 1
    assert error.value.report["collections_incomplete"] == {"trades": 1}


def test_verify_rejects_corrupt_zip(tmp_path):
    snapshot_path = tmp_path / "corrupt.zip"
    snapshot_path.write_bytes(b"not a zip")

    result = verify_snapshot(snapshot_path)

    assert result["verified"] is False
    assert result["errors"]


@pytest.mark.parametrize("filename", ["../MannatMoon_Snapshot_2026.zip", "snapshot.zip", "MannatMoon_Snapshot_../../x.zip"])
def test_snapshot_filename_validation_rejects_unsafe_names(filename):
    assert is_snapshot_filename(filename) is False


def test_snapshot_filename_validation_accepts_generated_name():
    assert is_snapshot_filename("MannatMoon_Snapshot_2026-09-27_150000.zip") is True