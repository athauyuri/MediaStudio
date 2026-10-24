from pathlib import Path
import subprocess
import sys

import pytest

from app.database.connection import Database
from app.database.repositories import SettingsRepository, SourceRepository, AssetRepository


ROOT = Path(__file__).resolve().parents[1]


def run(code, *args, cwd=ROOT):
    return subprocess.run([sys.executable, "-c", code, *map(str, args)], cwd=cwd,
                          capture_output=True, text=True, timeout=20)


def test_imports_do_not_touch_files(tmp_path):
    code = '''
import sys
sys.path.insert(0, sys.argv[1])
import app.database.connection
import app.database.migrations
import app.database.repositories
import app.main
'''
    result = run(code, ROOT, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["parent_file", "directory", "invalid_db", "migration"])
def test_startup_failure_does_not_show_window(tmp_path, failure):
    db_path = tmp_path / "owned" / "db.sqlite"
    if failure == "parent_file":
        db_path.parent.write_text("existing file")
    elif failure == "directory":
        db_path.mkdir(parents=True)
    elif failure == "invalid_db":
        db_path.parent.mkdir()
        db_path.write_bytes(b"invalid database; preserve these bytes")
    config = tmp_path / "test.toml"
    # TOML literal strings preserve Windows backslashes.
    config.write_text("database_path = '" + str(db_path) + "'", encoding="utf-8")
    code = '''
import sys
import app.main as entry
from app.database.migrations import MIGRATIONS, Migration
def forbidden(*args):
    raise AssertionError("Normal application window must not be constructed")
entry.MainWindow = forbidden
if sys.argv[3] == "migration":
    original = entry.Database.initialize
    def broken(database):
        return original(database, MIGRATIONS + (Migration(len(MIGRATIONS) + 1, "broken", ("BAD SQL",)),))
    entry.Database.initialize = broken
raise SystemExit(entry.main(["--runtime-root", sys.argv[1], "--config", sys.argv[2]]))
'''
    result = run(code, tmp_path, config, failure)
    assert result.returncode == 1, result.stderr
    assert "Media Studio startup failed" in result.stderr
    assert "No media files were changed" in result.stderr
    assert "Database startup failed" in (tmp_path / "logs" / "media-studio.log").read_text()
    if failure == "invalid_db":
        assert db_path.read_bytes() == b"invalid database; preserve these bytes"


def test_repeated_startup_does_not_access_registered_media(tmp_path):
    media = tmp_path / "user_media"
    media.mkdir()
    original = media / "original.png"
    original.write_bytes(b"preserve user media")
    runtime = tmp_path / "runtime"
    database = Database(runtime / "data" / "media_studio.db")
    database.initialize()
    source_id = SourceRepository(database).create(name="media", root_path=str(media))
    asset_id = AssetRepository(database).create(source_id=source_id,
        absolute_path=str(original), relative_path=original.name, filename=original.name,
        extension=".png", media_type="image")
    SettingsRepository(database).set("preserved", "yes")
    database.close()
    code = '''
import sys, os
from PySide6.QtCore import QTimer
import app.main as entry
media = os.path.normcase(os.path.abspath(sys.argv[2]))
def is_media(path):
    if not isinstance(path, (str, bytes, os.PathLike)):
        return False
    path = os.path.normcase(os.path.abspath(os.fsdecode(path)))
    return path == media or path.startswith(media + os.sep)
def audit(event, args):
    if event in {"open", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.mkdir"}:
        if any(is_media(arg) for arg in args):
            raise AssertionError("Startup accessed media: " + event)
sys.addaudithook(audit)
for name in ("stat", "lstat"):
    original = getattr(os, name)
    def guarded(path, *args, _original=original, **kwargs):
        if is_media(path):
            raise AssertionError("Startup inspected media")
        return _original(path, *args, **kwargs)
    setattr(os, name, guarded)
show = entry.MainWindow.show
def display(window):
    show(window)
    QTimer.singleShot(100, window.close)
entry.MainWindow.show = display
raise SystemExit(entry.main(["--runtime-root", sys.argv[1]]))
'''
    for _ in range(2):
        result = run(code, runtime, media)
        assert result.returncode == 0, result.stderr
    reopened = Database(runtime / "data" / "media_studio.db")
    reopened.initialize()
    assert SettingsRepository(reopened).get("preserved")["value"] == "yes"
    assert AssetRepository(reopened).get(asset_id)["absolute_path"] == str(original)
    reopened.close()
    assert original.read_bytes() == b"preserve user media"
    assert list(media.iterdir()) == [original]
