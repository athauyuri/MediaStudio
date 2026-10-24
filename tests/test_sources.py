from concurrent.futures import ThreadPoolExecutor

import pytest

from app.database.errors import DatabaseError
from app.database.migrations import MIGRATIONS
from app.database.connection import Database
from app.database.repositories import AssetRepository, SourceRepository, SettingsRepository
from app.services.source_errors import SourceError
from app.services.sources import SourceService
from app.utils.windows_paths import normalize_windows_path


@pytest.mark.parametrize("path", ["D:\\Media", "d:\\media\\", "D:/Media", "d:\\Media\\.\\"])
def test_path_variants_share_identity(path):
    assert normalize_windows_path(path).key == "d:\\media"
    assert normalize_windows_path(path).display[0] == "D"


def test_roots_unicode_unc_and_extended_namespace():
    assert normalize_windows_path("d:/").display == "D:\\"
    assert normalize_windows_path("D:/媒体/项目/").display == "D:\\媒体\\项目"
    assert normalize_windows_path("D:/媒体/项目/").key == "d:\\媒体\\项目"
    assert normalize_windows_path("\\\\?\\D:\\Media").key == "d:\\media"
    assert normalize_windows_path("\\\\server\\share").display == "\\\\server\\share\\"
    assert normalize_windows_path("\\\\?\\UNC\\server\\share\\media").key == "\\\\server\\share\\media"


@pytest.mark.parametrize("path", ["", "relative", "D:Media", "\\Media", "D:\\bad.\\", "D:\\bad*", "D:\x00bad"])
def test_invalid_paths_rejected(path):
    with pytest.raises(ValueError):
        normalize_windows_path(path)


def test_registration_name_enable_disable_and_safe_removal(source_setup):
    service, resolver, database = source_setup
    resolver.add("E:\\Media")
    row = service.register("e:/Media/", "Archive")
    key, source_id = row["source_identity_key"], row["source_id"]
    assert row["root_path"] == "E:\\Media"
    assert row["online"] == 1 and row["last_scan"] is None and row["last_seen_at"]
    assert SourceService.status(row) == "Online"
    calls = resolver.calls[:]
    row = service.rename(source_id, "New display name")
    assert row["source_identity_key"] == key
    row = service.set_enabled(source_id, False)
    assert SourceService.status(row) == "Disabled"
    assert service.repository.eligible_page() == []
    assert len(service.list_sources()) == 1
    row = service.set_enabled(source_id, True)
    assert len(service.repository.eligible_page()) == 1
    assert row["source_identity_key"] == key
    service.remove(source_id)
    assert service.list_sources() == []
    assert resolver.calls == calls  # Name/state/removal do not touch filesystem.


@pytest.mark.parametrize("variant", ["e:/media", "E:\\MEDIA\\", "e:\\Media"])
def test_duplicate_variants_meaningful_error(source_setup, variant):
    service, resolver, _ = source_setup
    resolver.add("E:\\Media")
    service.register("E:\\Media", "Archive")
    with pytest.raises(SourceError) as error:
        service.register(variant)
    assert error.value.error_code == "DUPLICATE_SOURCE"
    assert "Archive" in error.value.user_message
    assert len(service.list_sources()) == 1


def test_drive_letter_change_preserves_id_and_prevents_duplicates(source_setup):
    service, resolver, _ = source_setup
    resolver.add("E:\\Media")
    row = service.register("E:\\Media", "Archive")
    resolver.roots.clear()
    resolver.add("F:\\Media")
    with pytest.raises(SourceError, match="already registered"):
        service.register("F:\\Media")
    changed = service.refresh(row["source_id"])
    assert changed["source_id"] == row["source_id"]
    assert changed["source_identity_key"] == row["source_identity_key"]
    assert changed["root_path"] == "F:\\Media"
    assert changed["current_mount"] == "F:\\"
    assert changed["online"] == 1
    assert changed["name"] == "Archive"
    assert changed["last_seen_at"] >= row["last_seen_at"]
    assert len(service.list_sources()) == 1


def test_offline_keeps_source_assets_and_last_seen(source_setup):
    service, resolver, database = source_setup
    resolver.add("E:\\Media")
    row = service.register("E:\\Media")
    asset_id = AssetRepository(database).create(source_id=row["source_id"],
        absolute_path="E:\\Media\\item.png", relative_path="item.png", filename="item.png",
        extension=".png", media_type="image")
    resolver.mount_map.clear()
    offline = service.refresh(row["source_id"])
    assert SourceService.status(offline) == "Offline"
    assert offline["last_seen_at"] == row["last_seen_at"]
    assert offline["source_identity_key"] == row["source_identity_key"]
    assert AssetRepository(database).get(asset_id) is not None
    with pytest.raises(SourceError) as error:
        service.remove(row["source_id"])
    assert error.value.error_code == "SOURCE_REFERENCED"
    assert len(service.list_sources()) == 1


def test_wrong_volume_at_old_drive_is_not_adopted(source_setup):
    service, resolver, _ = source_setup
    resolver.add("E:\\Media")
    row = service.register("E:\\Media")
    resolver.add("E:\\Media", "\\\\?\\Volume{22222222-2222-2222-2222-222222222222}\\")
    refreshed = service.refresh(row["source_id"])
    assert not refreshed["online"]
    assert refreshed["volume_guid"] == row["volume_guid"]
    assert "does not match" in refreshed["status_message"]


def test_fallback_is_explicit_and_can_be_enriched(source_setup):
    service, resolver, _ = source_setup
    resolver.add("E:\\Media", None)
    row = service.register("E:\\Media")
    assert row["source_identity_key"] == "path:e:\\media"
    assert SourceService.status(row) == "Identity unresolved"
    assert "fallback" in row["status_message"]
    resolver.add("E:\\Media")
    enriched = service.refresh(row["source_id"])
    assert SourceService.status(enriched) == "Online"
    assert enriched["source_id"] == row["source_id"]


@pytest.mark.parametrize("first,second", [("E:\\Media", "E:\\Media\\Projects"),
    ("E:\\Media\\Projects", "E:\\Media"), ("E:\\", "E:\\Media")])
def test_nested_requires_confirmation_but_is_allowed(source_setup, first, second):
    service, resolver, _ = source_setup
    resolver.add(first)
    resolver.add(second)
    service.register(first)
    with pytest.raises(SourceError) as error:
        service.register(second)
    assert error.value.error_code == "NESTED_CONFIRMATION"
    assert len(service.list_sources()) == 1
    service.register(second, allow_nested=True)
    assert len(service.list_sources()) == 2


def test_sibling_prefix_not_nested_and_alias_mount_is_nested(source_setup):
    service, resolver, _ = source_setup
    resolver.add("E:\\Media")
    service.register("E:\\Media")
    resolver.add("E:\\MediaOther")
    service.register("E:\\MediaOther")
    resolver.add("F:\\Media\\Projects")
    with pytest.raises(SourceError) as error:
        service.register("F:\\Media\\Projects")
    assert error.value.error_code == "NESTED_CONFIRMATION"


def test_missing_access_errors_and_refresh_isolation(source_setup):
    service, resolver, _ = source_setup
    with pytest.raises(SourceError) as error:
        service.register("E:\\Missing")
    assert error.value.error_code == "SOURCE_MISSING"
    resolver.add("E:\\Media")
    row = service.register("E:\\Media")
    resolver.roots["e:\\media"] = SourceError("ACCESS_DENIED", "Access to this folder is denied.")
    with pytest.raises(SourceError) as error:
        service.register("E:\\Media")
    assert error.value.error_code == "ACCESS_DENIED"
    refreshed = service.refresh(row["source_id"])
    assert not refreshed["online"]
    assert "denied" in refreshed["status_message"]
    assert len(service.list_sources()) == 1


def test_concurrent_registration_duplicate_check_is_atomic(source_setup):
    service, resolver, _ = source_setup
    resolver.add("E:\\Media")
    def attempt(_):
        try:
            return service.register("E:\\Media")["source_id"]
        except SourceError as error:
            return error.error_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count("DUPLICATE_SOURCE") == 1
    assert len(service.list_sources()) == 1


def test_migration2_preserves_real_m1_data_and_legacy_duplicates(tmp_path):
    database = Database(tmp_path / "m1.db")
    database.initialize(MIGRATIONS[:1])
    repo = SourceRepository(database)
    ids = [repo.create(name="legacy", root_path=path) for path in
           ("E:\\Media", "e:/MEDIA/", "X:\\Unique", "bad relative path")]
    SettingsRepository(database).set("preserve", "yes")
    asset_id = AssetRepository(database).create(source_id=ids[0], absolute_path="E:\\Media\\x.png",
        relative_path="x.png", filename="x.png", extension=".png", media_type="image")
    with database.session() as connection:
        original_history = tuple(connection.execute("SELECT * FROM schema_migrations").fetchone())
    database.initialize()
    database.initialize()
    rows = repo.page()
    assert [row["source_id"] for row in rows] == ids
    assert rows[0]["source_identity_key"] is None
    assert rows[1]["source_identity_key"] is None
    assert rows[2]["source_identity_key"] == "path:x:\\unique"
    assert rows[3]["normalized_root_path"] is None
    assert all(row["volume_guid"] is None for row in rows)
    assert SettingsRepository(database).get("preserve")["value"] == "yes"
    assert AssetRepository(database).get(asset_id)["source_id"] == ids[0]
    with database.session() as connection:
        assert tuple(connection.execute("SELECT * FROM schema_migrations WHERE version=1").fetchone()) == original_history
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()


def test_identity_unique_index_case_insensitive(source_setup):
    service, resolver, database = source_setup
    resolver.add("E:\\Media")
    row = service.register("E:\\Media")
    with pytest.raises(DatabaseError):
        service.repository.create(name="duplicate", root_path="F:\\Media",
                                   source_identity_key=row["source_identity_key"].upper())
