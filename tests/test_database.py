from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.database.connection import Database
from app.database.errors import DatabaseError
from app.database.migrations import MIGRATIONS, Migration
from app.database.repositories import AssetRepository, SettingsRepository, SourceRepository


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "应用 data" / "媒体.db")
    database.initialize()
    yield database
    database.close()


def source(database):
    return SourceRepository(database).create(name="媒体", root_path=r"X:\不存在\媒体")


def asset(database, source_id, filename="测试.png"):
    return AssetRepository(database).create(source_id=source_id,
        absolute_path="X:\\不存在\\媒体\\" + filename, relative_path=filename,
        filename=filename, extension=".png", media_type="image")


def test_construct_does_not_create_database(tmp_path):
    database = Database(tmp_path / "absent" / "db.sqlite")
    assert not database.path.parent.exists()
    with pytest.raises(RuntimeError):
        with database.session():
            pass


def test_fresh_schema_pragmas_history_indexes(database):
    with database.session() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA encoding").fetchone()[0] == "UTF-8"
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"settings", "sources", "assets", "schema_migrations"} <= tables
        history = connection.execute("SELECT * FROM schema_migrations").fetchall()
        assert len(history) == len(MIGRATIONS)
        assert history[0]["version"] == 1
        assert history[0]["checksum"] == MIGRATIONS[0].checksum
        assert history[0]["applied_at"]
        indexed = {tuple(row[2] for row in connection.execute(
            f'PRAGMA index_info("{index[1]}")'))
            for index in connection.execute("PRAGMA index_list(assets)").fetchall()}
        assert {(field,) for field in ("source_id", "absolute_path", "filename",
            "media_type", "modified_time", "content_hash", "missing", "managed")} <= indexed


def test_reinitialize_preserves_records_and_history(database):
    repo = SettingsRepository(database)
    repo.set("theme", "dark")
    with database.session() as connection:
        before = tuple(connection.execute("SELECT * FROM schema_migrations").fetchone())
    database.initialize()
    assert repo.get("theme")["value"] == "dark"
    with database.session() as connection:
        assert tuple(connection.execute("SELECT * FROM schema_migrations").fetchone()) == before
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_commit_rollback_and_connection_close(database):
    with database.transaction() as connection:
        connection.execute("INSERT INTO settings(key,value) VALUES ('a','committed')")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="rollback"):
        with database.transaction() as connection:
            connection.execute("INSERT INTO settings(key,value) VALUES ('b','discarded')")
            raise RuntimeError("rollback")
    assert SettingsRepository(database).get("a")["value"] == "committed"
    assert SettingsRepository(database).get("b") is None
    with pytest.raises(DatabaseError) as error:
        with database.transaction() as connection:
            connection.execute("INSERT INTO settings(key,value) VALUES ('c','discarded')")
            connection.execute("INSERT INTO settings(key,value) VALUES ('a','duplicate')")
    assert error.value.error_code == "SQLITE_CONSTRAINT_PRIMARYKEY"
    assert SettingsRepository(database).get("c") is None


def test_foreign_keys_restrict_source_deletion(database):
    with pytest.raises(DatabaseError):
        asset(database, 999)
    source_id = source(database)
    asset_id = asset(database, source_id)
    with pytest.raises(DatabaseError):
        SourceRepository(database).delete(source_id)
    assert AssetRepository(database).get(asset_id) is not None


def test_failed_migration_rolls_back_ddl_and_history(database):
    broken = Migration(len(MIGRATIONS) + 1, "broken", ("CREATE TABLE should_rollback(x TEXT)",
                                    "INSERT INTO nonexistent VALUES (1)"))
    with pytest.raises(DatabaseError):
        database.initialize(MIGRATIONS + (broken,))
    with database.session() as connection:
        assert connection.execute("SELECT version FROM schema_migrations").fetchall()[0][0] == 1
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(MIGRATIONS)
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone() is None
    database.initialize()


def test_failed_fresh_migration_not_applied(tmp_path):
    database = Database(tmp_path / "fresh.db")
    with pytest.raises(DatabaseError):
        database.initialize((Migration(1, "broken", ("CREATE TABLE partial(x)", "BAD SQL")),))
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    database.initialize()
    database.close()


@pytest.mark.parametrize("migrations", [
    (), (Migration(1, "altered", ("SELECT 1",)),),
    (Migration(2, "unordered", ("SELECT 1",)),)])
def test_incompatible_history_is_rejected(database, migrations):
    with pytest.raises(DatabaseError):
        database.initialize(migrations)


def test_settings_crud_unicode_and_bound_parameters(database):
    repo = SettingsRepository(database)
    key = "媒体'; DROP TABLE assets; --"
    assert repo.get(key) is None
    repo.set(key, "值")
    assert repo.get(key)["value"] == "值"
    assert repo.get(key)["updated_at"]
    repo.set(key, "新值")
    assert repo.get(key)["value"] == "新值"
    assert repo.delete(key)
    assert not repo.delete(key)
    assert repo.get(key) is None


def test_source_crud_stable_ids_and_paging(database):
    repo = SourceRepository(database)
    first = source(database)
    row = repo.get(first)
    assert row["online"] == 0
    assert row["enabled"] == 1
    assert row["created_at"] and row["updated_at"]
    assert repo.update(first, root_path=r"Y:\媒体", name="renamed", enabled=False)
    assert repo.get(first)["source_id"] == first
    assert repo.get(first)["root_path"] == r"Y:\媒体"
    second = repo.create(name="second", root_path=r"Z:\other")
    assert len(repo.page(limit=1)) == 1
    assert repo.page(after_id=first)[0]["source_id"] == second
    assert repo.delete(second)
    third = repo.create(name="third", root_path=r"Z:\other")
    assert third > second  # Never reuse deleted identifiers.
    assert repo.get(second) is None
    assert not repo.update(second, name="missing")
    with pytest.raises(ValueError):
        repo.update(first, source_id=999)
    with pytest.raises(ValueError):
        repo.page(limit=1001)


def test_asset_crud_nullable_metadata_and_stable_id(database):
    repo = AssetRepository(database)
    source_id = source(database)
    record_id = asset(database, source_id)
    row = repo.get(record_id)
    assert row["width"] is None and row["file_size"] is None
    assert row["rating"] == 0 and row["missing"] == 0
    assert repo.update(record_id, absolute_path=r"Y:\new.png", filename="new.png",
                       width=1920, height=1080, favorite=True)
    assert repo.get(record_id)["asset_id"] == record_id
    assert repo.page()[0]["favorite"] == 1
    with pytest.raises(DatabaseError):
        repo.update(record_id, rating=6)
    assert repo.get(record_id)["rating"] == 0
    assert repo.delete(record_id)
    assert repo.get(record_id) is None
    assert SourceRepository(database).delete(source_id)


def test_thread_sessions_and_concurrent_initialization(database):
    def worker(index):
        other = Database(database.path)
        other.initialize()
        SettingsRepository(other).set(str(index), "worker")
        with other.session() as connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        other.close()
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(worker, range(6)))
    for index in range(6):
        assert SettingsRepository(database).get(str(index))["value"] == "worker"


def test_shutdown_rejects_new_sessions_and_does_not_recreate(database):
    path = database.path
    database.close()
    database.close()
    with pytest.raises(RuntimeError):
        with database.session():
            pass
    # Fully closed SQLite handles allow a temporary test DB rename on Windows.
    renamed = path.with_suffix(".closed")
    path.rename(renamed)
    assert renamed.is_file()


def test_missing_initialized_file_not_silently_recreated(database):
    database.path.rename(database.path.with_suffix(".backup"))
    with pytest.raises(DatabaseError):
        with database.session():
            pass
    assert not database.path.exists()
