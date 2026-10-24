from dataclasses import replace
from pathlib import Path
from threading import Event
import os
import ntpath
import stat
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from app.database.connection import Database
from app.database.errors import DatabaseError
from app.database.migrations import MIGRATIONS
from app.database.repositories import AssetRepository, SettingsRepository, SourceRepository
from app.database.repositories.scans import ScanRepository
from app.services.media_types import media_type, SUPPORTED_EXTENSIONS
from app.services.scan_errors import ScanError
from app.services.scan_policy import ScanPolicy
from app.services.scanner import ScanService, Filesystem
from app.services.source_errors import SourceError
from app.utils.windows_paths import normalize_windows_path


@pytest.fixture
def scan_setup(source_setup, tmp_path):
    sources, resolver, database = source_setup
    root = tmp_path / "media"
    root.mkdir()
    resolver.add(str(root))
    row = sources.register(str(root))
    scanner = ScanService(ScanRepository(database), sources, ScanPolicy(chunk_size=3))
    return scanner, root, row, resolver, database


def fixture_asset(database, row, relative, **values):
    path = Path(row["root_path"]) / relative
    return AssetRepository(database).create(source_id=row["source_id"], absolute_path=str(path),
        relative_path=relative, filename=path.name, extension=path.suffix, media_type=media_type(path.name), **values)


@pytest.mark.parametrize("extension", sorted(SUPPORTED_EXTENSIONS))
def test_extensions_case_insensitive(extension):
    assert media_type("MEDIA" + extension.upper()) in {"image", "video"}
    assert media_type("ignored.txt") is None
    assert media_type("x.png.txt") is None


def test_recursive_index_and_repeated_scan_stability(scan_setup):
    scanner, root, row, _, database = scan_setup
    nested = root / "子目录"
    nested.mkdir()
    (nested / "图片.PNG").write_bytes(b"image content is not decoded")
    (root / "movie.MP4").write_bytes(b"video content is not decoded")
    (root / "ignored.txt").write_text("ignored")
    first = scanner.scan(row["source_id"])
    assert first["status"] == "COMPLETED" and first["reconciliation_performed"]
    assert first["directories_seen"] == 2 and first["files_seen"] == 3
    assert first["supported_files"] == 2 and first["files_indexed"] == 2
    assert first["files_skipped"] == 1
    assets = AssetRepository(database).page()
    assert len(assets) == 2
    assert {asset["media_type"] for asset in assets} == {"image", "video"}
    for asset in assets:
        assert asset["file_size"] > 0 and asset["created_time"] and asset["modified_time"]
        for field in ("width", "height", "duration", "fps", "content_hash", "quick_hash", "thumbnail_path", "poster_path", "video_codec"):
            assert asset[field] is None
    second = scanner.scan(row["source_id"])
    assert second["files_indexed"] == second["files_updated"] == 0
    assert [asset["asset_id"] for asset in AssetRepository(database).page()] == [asset["asset_id"] for asset in assets]
    assert scanner.sources._get(row["source_id"])["last_scan"]


def test_update_preserves_unrelated_metadata_missing_and_case(scan_setup):
    scanner, root, row, _, database = scan_setup
    (root / "photo.PNG").write_bytes(b"current content")
    asset_id = fixture_asset(database, row, "photo.PNG", file_size=1, missing=True,
        width=1920, height=1080, duration=3.5, content_hash="future hash",
        quick_hash="future quick", thumbnail_path="future.webp", rating=4, favorite=True, managed=True)
    result = scanner.scan(row["source_id"])
    assert result["files_updated"] == 1 and result["files_indexed"] == 0
    asset = AssetRepository(database).get(asset_id)
    assert not asset["missing"] and asset["file_size"] == len(b"current content")
    assert asset["width"] == 1920 and asset["duration"] == 3.5
    assert asset["content_hash"] == "future hash" and asset["thumbnail_path"] == "future.webp"
    assert asset["rating"] == 4 and asset["favorite"] and asset["managed"]
    # Case-only filesystem rename must not allocate a second ID.
    (root / "photo.PNG").rename(root / "PHOTO.png")
    scanner.scan(row["source_id"])
    assert len(AssetRepository(database).page()) == 1
    assert AssetRepository(database).get(asset_id)["filename"] == "PHOTO.png"


def test_completed_scan_marks_unseen_missing_and_reappearance(scan_setup):
    scanner, root, row, _, database = scan_setup
    asset_id = fixture_asset(database, row, "absent.png", width=10, content_hash="preserve")
    result = scanner.scan(row["source_id"])
    assert result["missing_marked"] == 1
    assert AssetRepository(database).get(asset_id)["missing"] == 1
    (root / "absent.png").write_bytes(b"now present")
    scanner.scan(row["source_id"])
    assert AssetRepository(database).get(asset_id)["missing"] == 0
    assert AssetRepository(database).get(asset_id)["content_hash"] == "preserve"


def test_cancelled_scan_retains_committed_discoveries_without_missing_reconciliation(scan_setup, monkeypatch):
    scanner, root, row, _, database = scan_setup
    absent = fixture_asset(database, row, "absent.png")
    for index in range(10):
        (root / f"{index}.png").write_bytes(b"sample")
    cancel = Event()
    original = scanner.repository.write_chunk
    def commit(*args):
        result = original(*args)
        if args[5]["files_indexed"] >= 3:
            cancel.set()
        return result
    monkeypatch.setattr(scanner.repository, "write_chunk", commit)
    result = scanner.scan(row["source_id"], cancel=cancel)
    assert result["status"] == "CANCELLED"
    assert not result["reconciliation_performed"]
    assert result["files_indexed"] >= 3
    assert not AssetRepository(database).get(absent)["missing"]
    assert scanner.sources._get(row["source_id"])["last_scan"] is None


def test_source_disconnect_is_fatal_and_does_not_reconcile(scan_setup):
    scanner, root, row, resolver, database = scan_setup
    absent = fixture_asset(database, row, "absent.png")
    (root / "present.png").write_bytes(b"sample")
    def disconnect(value):
        resolver.roots.clear()
    result = scanner.scan(row["source_id"], progress=disconnect)
    assert result["status"] == "FAILED"
    assert not result["reconciliation_performed"]
    assert not AssetRepository(database).get(absent)["missing"]
    assert scanner.issues(result["scan_id"])[0]["category"] == "SOURCE_MISSING"


def test_inaccessible_child_and_metadata_failure_suppress_missing(scan_setup):
    scanner, root, row, _, database = scan_setup
    child = root / "denied"
    child.mkdir()
    (root / "visible.png").write_bytes(b"sample")
    absent = fixture_asset(database, row, "denied\\unseen.png")
    class DeniedFilesystem(Filesystem):
        def entries(self, path):
            if Path(path).name == "denied":
                raise PermissionError("test access denied")
            return os.scandir(path)
    scanner.filesystem = DeniedFilesystem()
    result = scanner.scan(row["source_id"])
    assert result["status"] == "COMPLETED" and result["errors"] == 1
    assert not result["coverage_complete"] and not result["reconciliation_performed"]
    assert not AssetRepository(database).get(absent)["missing"]
    issue = scanner.issues(result["scan_id"])[0]
    assert issue["category"] == "ACCESS_DENIED" and issue["timestamp"]


def test_active_source_conflicts_and_duplicate_scan_prevention(scan_setup):
    scanner, _, row, _, database = scan_setup
    scanner.repository.claim(row, 1)
    with pytest.raises(ScanError) as error:
        scanner.scan(row["source_id"])
    assert error.value.error_code == "SCAN_ACTIVE"
    for operation in (lambda: scanner.sources.set_enabled(row["source_id"], False),
                      lambda: scanner.sources.remove(row["source_id"]),
                      lambda: scanner.sources.rename(row["source_id"], "changed"),
                      lambda: scanner.sources.refresh(row["source_id"])):
        with pytest.raises(SourceError) as error:
            operation()
        assert error.value.error_code == "SCAN_ACTIVE"
    with pytest.raises(DatabaseError):
        SourceRepository(database).delete(row["source_id"])


def test_disabled_and_offline_sources_cannot_start(scan_setup):
    scanner, _, row, resolver, _ = scan_setup
    scanner.sources.set_enabled(row["source_id"], False)
    with pytest.raises(ScanError) as error:
        scanner.scan(row["source_id"])
    assert error.value.error_code == "SOURCE_DISABLED"
    scanner.sources.set_enabled(row["source_id"], True)
    resolver.mount_map.clear()
    with pytest.raises(ScanError) as error:
        scanner.scan(row["source_id"])
    assert error.value.error_code == "SOURCE_OFFLINE"
    assert scanner.latest(row["source_id"]) is None


def test_app_owned_exclusions_and_protected_root(scan_setup):
    scanner, root, row, _, database = scan_setup
    cache = root / "cache"
    cache.mkdir()
    (cache / "must_not_index.png").write_bytes(b"derived content")
    (root / "user.png").write_bytes(b"user content")
    scanner.policy = ScanPolicy((str(cache),), chunk_size=3)
    result = scanner.scan(row["source_id"])
    assert result["files_indexed"] == 1 and not result["reconciliation_performed"]
    assert scanner.issues(result["scan_id"])[0]["category"] == "APPLICATION_PATH_EXCLUDED"
    assert AssetRepository(database).page()[0]["filename"] == "user.png"
    scanner.policy = ScanPolicy((str(root),))
    with pytest.raises(ScanError) as error:
        scanner.scan(row["source_id"])
    assert error.value.error_code == "APPLICATION_PATH_EXCLUDED"


def test_bounded_stress_chunks(scan_setup, monkeypatch):
    scanner, root, row, _, database = scan_setup
    for index in range(600):
        (root / f"file{index}.png").write_bytes(b"x")
    scanner.policy = ScanPolicy(chunk_size=32)
    sizes = []
    original = scanner.repository.write_chunk
    def counted(scan_id, source_id, assets, directories, issues, counters, folder):
        sizes.append(len(assets) + len(directories) + len(issues))
        return original(scan_id, source_id, assets, directories, issues, counters, folder)
    monkeypatch.setattr(scanner.repository, "write_chunk", counted)
    result = scanner.scan(row["source_id"])
    assert result["files_indexed"] == 600
    assert max(sizes) <= 32 and sum(size > 0 for size in sizes) >= 19
    assert len(AssetRepository(database).page(limit=100)) == 100


def test_migration3_upgrades_m3_without_filesystem_or_data_loss(tmp_path):
    database = Database(tmp_path / "old.db")
    database.initialize(MIGRATIONS[:2])
    sources = SourceRepository(database)
    source_id = sources.create(name="preserve", root_path="E:\\Media", volume_guid="GUID",
        source_identity_key="volume:guid|media", normalized_root_path="e:\\media")
    asset_id = AssetRepository(database).create(source_id=source_id,
        absolute_path="E:\\Media\\媒体.PNG", relative_path="媒体.PNG", filename="媒体.PNG", extension=".PNG", media_type="image", width=100)
    SettingsRepository(database).set("existing", "value")
    with database.session() as connection:
        old = [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")]
    database.initialize()
    database.initialize()
    with database.session() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations WHERE version<=2")] == old
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert sources.get(source_id)["source_identity_key"] == "volume:guid|media"
    assert AssetRepository(database).get(asset_id)["normalized_relative_path"] == "媒体.png"
    assert AssetRepository(database).get(asset_id)["width"] == 100
    assert SettingsRepository(database).get("existing")["value"] == "value"
    database.close()


def fake_metadata(mode=stat.S_IFREG, attributes=0):
    return SimpleNamespace(st_mode=mode, st_file_attributes=attributes,
        st_size=7, st_ctime=1_700_000_000, st_mtime=1_700_000_000)


def test_reparse_entries_skip_without_following_loops(scan_setup):
    scanner, root, row, _, _ = scan_setup
    class Entry:
        def __init__(self, name, mode):
            self.name, self.path, self.mode = name, str(root / name), mode
        def stat(self, *, follow_symlinks):
            assert follow_symlinks is False
            return fake_metadata(self.mode, stat.FILE_ATTRIBUTE_REPARSE_POINT)
    class LoopFilesystem(Filesystem):
        @contextmanager
        def entries(self, path):
            assert normalize_windows_path(path).key == row["normalized_root_path"], "Followed a link!"
            yield iter((Entry("junction_to_parent", stat.S_IFDIR), Entry("symlink.png", stat.S_IFLNK)))
    scanner.filesystem = LoopFilesystem()
    result = scanner.scan(row["source_id"])
    assert result["status"] == "COMPLETED" and result["files_indexed"] == 0
    assert result["files_skipped"] == 2 and result["directories_seen"] == 1
    assert not result["reconciliation_performed"]
    assert {issue["category"] for issue in scanner.issues(result["scan_id"])} == {"REPARSE_SKIPPED"}


def test_deep_iterative_tree_never_accumulates_open_iterators(scan_setup):
    scanner, root, row, _, _ = scan_setup
    class Entry:
        def __init__(self, path, directory):
            self.path, self.name = path, ntpath.basename(path)
            self.directory = directory
        def stat(self, **kwargs):
            return fake_metadata(stat.S_IFDIR if self.directory else stat.S_IFREG)
    class DeepFilesystem:
        active = 0
        maximum = 0
        @staticmethod
        def directory_stat(path):
            return fake_metadata(stat.S_IFDIR)
        @contextmanager
        def entries(self, path):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            depth = path.count("\\") - row["root_path"].count("\\")
            try:
                yield iter((Entry(path + ("\\d" if depth < 1050 else "\\leaf.png"), depth < 1050),))
            finally:
                self.active -= 1
    filesystem = DeepFilesystem()
    scanner.filesystem = filesystem
    result = scanner.scan(row["source_id"])
    assert result["directories_seen"] == 1051
    assert result["files_indexed"] == 1
    assert filesystem.maximum == 1 and filesystem.active == 0


def test_visible_file_stat_failure_records_error_without_deceptive_asset(scan_setup):
    scanner, root, row, _, database = scan_setup
    class Entry:
        name, path = "unreadable.png", str(root / "unreadable.png")
        def stat(self, **kwargs):
            raise PermissionError("test metadata unavailable")
    class MetadataFailure(Filesystem):
        @contextmanager
        def entries(self, path):
            yield iter((Entry(),))
    scanner.filesystem = MetadataFailure()
    result = scanner.scan(row["source_id"])
    assert result["errors"] == 1 and result["files_indexed"] == 0
    assert AssetRepository(database).page() == []
    assert scanner.issues(result["scan_id"])[0]["category"] == "ACCESS_DENIED"


def test_global_scan_limit_and_terminal_transition(scan_setup, tmp_path):
    scanner, root, row, resolver, _ = scan_setup
    scanner.repository.claim(row, 1)
    second = tmp_path / "second"
    second.mkdir()
    resolver.add(str(second))
    another = scanner.sources.register(str(second))
    with pytest.raises(ScanError) as error:
        scanner.scan(another["source_id"])
    assert error.value.error_code == "SCAN_CAPACITY"


def test_unexpected_worker_failure_persists_failed_state(scan_setup):
    scanner, _, row, _, _ = scan_setup
    class BrokenFilesystem(Filesystem):
        def entries(self, path):
            raise RuntimeError("unexpected synthetic failure")
    scanner.filesystem = BrokenFilesystem()
    result = scanner.scan(row["source_id"])
    assert result["status"] == "FAILED" and result["errors"] == 1
    assert scanner.issues(result["scan_id"])[0]["category"] == "UNKNOWN"


def test_cancel_at_final_reconciliation_rolls_back_all_missing_changes(scan_setup, monkeypatch):
    scanner, _, row, _, database = scan_setup
    asset_id = fixture_asset(database, row, "absent.png")
    original = scanner.repository.finish
    cancel = Event()
    def finish(scan_id, status, counters, **kwargs):
        if status == "COMPLETED":
            cancel.set()
        return original(scan_id, status, counters, **kwargs)
    monkeypatch.setattr(scanner.repository, "finish", finish)
    result = scanner.scan(row["source_id"], cancel=cancel)
    assert result["status"] == "CANCELLED"
    assert not AssetRepository(database).get(asset_id)["missing"]


def test_source_id_survives_mock_drive_change_during_rediscovery(scan_setup):
    scanner, root, row, resolver, database = scan_setup
    (root / "file.png").write_bytes(b"x")
    scanner.scan(row["source_id"])
    asset_id = AssetRepository(database).page()[0]["asset_id"]
    # Test logical path matching independently of real drive hardware.
    with database.transaction() as connection:
        connection.execute("UPDATE assets SET absolute_path='Z:\\old\\file.png', normalized_absolute_path='z:\\old\\file.png' WHERE asset_id=?", (asset_id,))
    result = scanner.scan(row["source_id"])
    assert result["files_indexed"] == 0
    assert AssetRepository(database).get(asset_id)["absolute_path"] == str(root / "file.png")


def test_wrong_volume_is_rejected_before_creating_session(scan_setup):
    scanner, root, row, resolver, _ = scan_setup
    resolver.add(str(root), "\\\\?\\Volume{22222222-2222-2222-2222-222222222222}\\")
    with pytest.raises(ScanError) as error:
        scanner.scan(row["source_id"])
    assert error.value.error_code == "SOURCE_OFFLINE"
    assert scanner.latest(row["source_id"]) is None


def test_overlap_scan_does_not_reassign_another_sources_asset(scan_setup):
    scanner, root, row, resolver, database = scan_setup
    child = root / "child"
    child.mkdir()
    (child / "image.png").write_bytes(b"x")
    scanner.scan(row["source_id"])
    original = AssetRepository(database).page()[0]
    resolver.add(str(child))
    nested = scanner.sources.register(str(child), allow_nested=True)
    result = scanner.scan(nested["source_id"])
    assert result["files_indexed"] == 0
    assert result["errors"] == 1 and not result["reconciliation_performed"]
    assert AssetRepository(database).get(original["asset_id"])["source_id"] == row["source_id"]
    assert scanner.issues(result["scan_id"])[0]["category"] == "PATH_CONFLICT"
