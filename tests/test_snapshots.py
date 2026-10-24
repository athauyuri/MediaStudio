from threading import Event

import pytest

from app.database.connection import Database
from app.database.errors import DatabaseError
from app.database.migrations import MIGRATIONS
from app.database.repositories import AssetRepository, SourceRepository, SettingsRepository
from app.database.repositories.snapshots import SnapshotRepository
from app.services.scan_errors import ScanError
from app.services.scanner import Filesystem
from app.services.snapshots import SnapshotService
from test_scanner import scan_setup, fixture_asset


def build_sample(scanner, root, source):
    child = root / "子"
    child.mkdir()
    (root / "a.PNG").write_bytes(b"image")
    (child / "b.MP4").write_bytes(b"video bytes")
    (root / "ignore.txt").write_bytes(b"ignored")
    return scanner.scan(source["source_id"])


def test_complete_snapshot_summary_manifest_and_folders(scan_setup):
    scanner, root, source, _, _ = scan_setup
    scan = build_sample(scanner, root, source)
    assert not scan.get("snapshot_error"), scan.get("snapshot_error")
    summary = scanner.snapshots.summary(scan["snapshot_id"])
    assert summary["state"] == "FINALIZED"
    assert summary["coverage_status"] == "COMPLETE"
    assert summary["eligible_for_reorganization_baseline"] == 1
    assert summary["folder_count"] == 2 and summary["file_count"] == 3
    assert summary["supported_media_count"] == 2 and summary["unsupported_count"] == 1
    assert summary["image_count"] == summary["video_count"] == 1
    assert summary["total_bytes"] == len(b"image") + len(b"video bytes")
    assert summary["skipped_count"] == summary["error_count"] == 0
    assert summary["source_identity_key"] == source["source_identity_key"]
    rows = scanner.snapshots.manifest(summary["snapshot_id"], limit=1)
    second = scanner.snapshots.manifest(summary["snapshot_id"], after_id=rows[0]["manifest_entry_id"], limit=1)
    assert len(rows) == len(second) == 1 and rows[0]["manifest_entry_id"] != second[0]["manifest_entry_id"]
    folders = scanner.snapshots.folders(summary["snapshot_id"])
    assert {row["folder_path"] for row in folders} == {"", "子"}
    assert sum(row["total_bytes"] for row in folders) == summary["total_bytes"]
    assert sum(row["supported_media_count"] for row in folders) == 2


def test_snapshot_values_survive_asset_mutation_rescan_source_rename_and_remount(scan_setup):
    scanner, root, source, resolver, database = scan_setup
    first = build_sample(scanner, root, source)
    original = scanner.snapshots.manifest(first["snapshot_id"])
    asset_id = original[0]["asset_id"]
    AssetRepository(database).update(asset_id, absolute_path="Z:\\new\\image.png", file_size=999)
    scanner.sources.rename(source["source_id"], "New name")
    (root / "a.PNG").write_bytes(b"changed size")
    second = scanner.scan(source["source_id"])
    assert second["snapshot_id"] != first["snapshot_id"]
    assert scanner.snapshots.manifest(first["snapshot_id"]) == original
    assert scanner.snapshots.summary(first["snapshot_id"])["source_name"] == source["name"]
    assert scanner.snapshots.summary(second["snapshot_id"])["source_name"] == "New name"
    # A source remount changes the current registration, never historical values.
    scanner.sources.repository.update(source["source_id"], root_path="Z:\\Media", current_mount="Z:\\")
    assert scanner.snapshots.summary(first["snapshot_id"])["source_root_at_scan"] == str(root)
    assert scanner.snapshots.manifest(first["snapshot_id"]) == original


def test_finalized_snapshot_and_manifest_reject_mutation(scan_setup):
    scanner, root, source, _, database = scan_setup
    scan = build_sample(scanner, root, source)
    snapshot_id = scan["snapshot_id"]
    entry = scanner.snapshots.manifest(snapshot_id)[0]
    statements = [
        ("UPDATE scan_snapshots SET total_bytes=0 WHERE snapshot_id=?", snapshot_id),
        ("DELETE FROM scan_snapshots WHERE snapshot_id=?", snapshot_id),
        ("UPDATE scan_manifest_entries SET file_size=0 WHERE snapshot_id=?", snapshot_id),
        ("DELETE FROM scan_manifest_entries WHERE snapshot_id=?", snapshot_id),
        ("UPDATE scan_observations SET file_size=0 WHERE scan_id=?", scan["scan_id"]),
        ("DELETE FROM scan_observations WHERE scan_id=?", scan["scan_id"]),
        ("UPDATE scan_sessions SET files_seen=0 WHERE scan_id=?", scan["scan_id"]),
    ]
    for sql, value in statements:
        with pytest.raises(DatabaseError):
            with database.transaction() as connection:
                connection.execute(sql, (value,))
    with pytest.raises(DatabaseError):
        with database.transaction() as connection:
            connection.execute("""INSERT INTO scan_manifest_entries(snapshot_id,absolute_path_at_scan,
                relative_path_at_scan,filename,extension,media_type,file_size,created_time,modified_time,
                comparison_key,folder_key,folder_path,observation_status)
                SELECT snapshot_id,absolute_path_at_scan,relative_path_at_scan,filename,extension,media_type,
                file_size,created_time,modified_time,comparison_key,folder_key,folder_path,observation_status
                FROM scan_manifest_entries WHERE manifest_entry_id=?""", (entry["manifest_entry_id"],))
    assert scanner.snapshots.finalize(scan["scan_id"])["snapshot_id"] == snapshot_id


def test_incomplete_snapshot_is_explicitly_ineligible_with_issue_review(scan_setup):
    scanner, root, source, _, _ = scan_setup
    child = root / "denied"
    child.mkdir()
    (root / "a.png").write_bytes(b"image")
    class Denied(Filesystem):
        def entries(self, path):
            if str(path).endswith("denied"):
                raise PermissionError("denied")
            return super().entries(path)
    scanner.filesystem = Denied()
    scan = scanner.scan(source["source_id"])
    summary = scanner.snapshots.summary(scan["snapshot_id"])
    assert summary["coverage_status"] == "INCOMPLETE"
    assert not summary["eligible_for_reorganization_baseline"]
    assert summary["skipped_count"] == summary["error_count"] == 1
    assert summary["supported_media_count"] == 1
    assert scanner.snapshots.issues(scan["scan_id"])[0]["category"] == "ACCESS_DENIED"


@pytest.mark.parametrize("kind", ["cancel", "fail"])
def test_cancelled_failed_scans_have_no_snapshot(scan_setup, kind):
    scanner, root, source, resolver, database = scan_setup
    cancel = Event()
    def interrupt(value):
        if kind == "cancel":
            cancel.set()
        else:
            resolver.roots.clear()
    scan = scanner.scan(source["source_id"], cancel=cancel, progress=interrupt)
    assert scan["status"] in {"CANCELLED", "FAILED"}
    with pytest.raises(ScanError):
        scanner.snapshots.finalize(scan["scan_id"])
    with database.session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scan_snapshots").fetchone()[0] == 0


def test_failed_snapshot_build_is_atomic_preserves_scan_and_can_retry(scan_setup, monkeypatch):
    scanner, root, source, _, database = scan_setup
    original = SnapshotRepository._populate
    def broken(connection, snapshot_id, scan_id):
        original(connection, snapshot_id, scan_id)
        raise RuntimeError("test snapshot failure after insert")
    monkeypatch.setattr(SnapshotRepository, "_populate", staticmethod(broken))
    scan = build_sample(scanner, root, source)
    assert scan["status"] == "COMPLETED" and "test snapshot failure" in scan["snapshot_error"]
    with database.session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scan_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM scan_manifest_entries").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM scan_observations").fetchone()[0] == 2
    monkeypatch.setattr(SnapshotRepository, "_populate", staticmethod(original))
    summary = scanner.snapshots.finalize(scan["scan_id"])
    assert summary["eligible_for_reorganization_baseline"]
    assert scanner.latest(source["source_id"])["snapshot_error"] is None


def test_legacy_m4_scan_cannot_be_fabricated_into_snapshot(tmp_path):
    database = Database(tmp_path / "old.db")
    database.initialize(MIGRATIONS[:3])
    source_id = SourceRepository(database).create(name="old", root_path="E:\\Media", source_identity_key="volume:old|media")
    asset_id = AssetRepository(database).create(source_id=source_id, absolute_path="E:\\Media\\a.png",
        relative_path="a.png", filename="a.png", extension=".png", media_type="image", width=100)
    SettingsRepository(database).set("preserve", "yes")
    with database.transaction() as connection:
        scan_id = connection.execute("INSERT INTO scan_sessions(source_id,root_path,status) VALUES (?,?,'COMPLETED')", (source_id, "E:\\Media")).lastrowid
        connection.execute("INSERT INTO scan_issues(scan_id,path,category,user_message,technical_message) VALUES (?,?,?,?,?)", (scan_id, "oldpath", "OLD", "old message", "old technical"))
        old = [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")]
    database.initialize()
    database.initialize()
    snapshots = SnapshotService(database)
    with pytest.raises(ScanError, match="no authoritative retained observations"):
        snapshots.finalize(scan_id)
    assert SourceRepository(database).get(source_id)["source_identity_key"] == "volume:old|media"
    assert AssetRepository(database).get(asset_id)["width"] == 100
    assert SettingsRepository(database).get("preserve")["value"] == "yes"
    assert snapshots.issues(scan_id)[0]["category"] == "OLD"
    with database.session() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations WHERE version<=3")] == old
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    database.close()


def test_delayed_build_uses_old_observations_after_new_scan_updates_assets(scan_setup):
    scanner, root, source, _, database = scan_setup
    (root / "a.png").write_bytes(b"old")
    original = scanner.snapshots.finalize
    scanner.snapshots.finalize = lambda scan_id: {"snapshot_id": None}
    old = scanner.scan(source["source_id"])
    scanner.snapshots.finalize = original
    (root / "a.png").write_bytes(b"new content with a new size")
    fresh = scanner.scan(source["source_id"])
    delayed = scanner.snapshots.finalize(old["scan_id"])
    assert delayed["total_bytes"] == len(b"old")
    assert scanner.snapshots.summary(fresh["snapshot_id"])["total_bytes"] == len(b"new content with a new size")
    assert scanner.snapshots.manifest(delayed["snapshot_id"])[0]["file_size"] == len(b"old")


def test_empty_snapshot_retention_blocks_source_removal_and_asset_delete_preserves_history(scan_setup):
    scanner, root, source, _, database = scan_setup
    empty = scanner.scan(source["source_id"])
    from app.services.source_errors import SourceError
    with pytest.raises(SourceError, match="retained scan/snapshot history"):
        scanner.sources.remove(source["source_id"])
    assert scanner.snapshots.summary(empty["snapshot_id"])["supported_media_count"] == 0
    (root / "a.png").write_bytes(b"x")
    scan = scanner.scan(source["source_id"])
    historical = scanner.snapshots.manifest(scan["snapshot_id"])[0]
    AssetRepository(database).delete(historical["asset_id"])
    assert scanner.snapshots.manifest(scan["snapshot_id"])[0] == historical


def test_path_conflicts_are_manifested_as_observed_but_ineligible(scan_setup):
    scanner, root, source, resolver, database = scan_setup
    child = root / "child"
    child.mkdir()
    (child / "a.png").write_bytes(b"observed content")
    scanner.scan(source["source_id"])
    resolver.add(str(child))
    nested = scanner.sources.register(str(child), allow_nested=True)
    scan = scanner.scan(nested["source_id"])
    summary = scanner.snapshots.summary(scan["snapshot_id"])
    assert scan["files_indexed"] == 0 and summary["supported_media_count"] == 1
    assert summary["total_bytes"] == len(b"observed content")
    assert summary["coverage_status"] == "INCOMPLETE"
    assert not summary["eligible_for_reorganization_baseline"]
    entry = scanner.snapshots.manifest(scan["snapshot_id"])[0]
    assert entry["asset_id"] is None and entry["observation_status"] == "PATH_CONFLICT"


def test_finalized_snapshot_issues_cannot_be_inserted_updated_or_deleted(scan_setup):
    scanner, root, source, _, database = scan_setup
    (root / "excluded").mkdir()
    from app.services.scan_policy import ScanPolicy
    scanner.policy = ScanPolicy((str(root / "excluded"),))
    scan = scanner.scan(source["source_id"])
    issue = scanner.snapshots.issues(scan["scan_id"])[0]
    for sql, params in (
        ("UPDATE scan_issues SET user_message='changed' WHERE issue_id=?", (issue["issue_id"],)),
        ("DELETE FROM scan_issues WHERE issue_id=?", (issue["issue_id"],)),
        ("INSERT INTO scan_issues(scan_id,path,category,user_message,technical_message) VALUES (?,'p','c','m','t')", (scan["scan_id"],))):
        with pytest.raises(DatabaseError):
            with database.transaction() as connection:
                connection.execute(sql, params)
    assert scanner.snapshots.issues(scan["scan_id"])[0] == issue
