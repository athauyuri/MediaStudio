from pathlib import Path
import subprocess
import sys


def test_native_registration_and_refresh_are_root_only(tmp_path):
    media = tmp_path / "media"
    nested = media / "child"
    nested.mkdir(parents=True)
    original = nested / "original.png"
    original.write_bytes(b"do not inspect or modify")
    code = '''
import os, sys
from app.database.connection import Database
from app.database.repositories.sources import SourceRepository
from app.services.sources import SourceService
from app.services.windows_volumes import WindowsVolumeResolver
from pathlib import Path
database = Database(Path(sys.argv[1]) / "owned" / "studio.db")
database.initialize()
root = os.path.normcase(os.path.abspath(sys.argv[2]))
def within(value):
    if not isinstance(value, (str, bytes, os.PathLike)):
        return False
    value = os.path.normcase(os.path.abspath(os.fsdecode(value)))
    return value == root or value.startswith(root + os.sep)
def audit(event, args):
    if event in {"open", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.mkdir"}:
        if any(within(value) for value in args):
            raise AssertionError("Forbidden source access: " + event)
sys.addaudithook(audit)
original_stat = os.stat
inspected = []
def root_stat(path, *args, **kwargs):
    if within(path):
        absolute = os.path.normcase(os.path.abspath(path))
        assert absolute == root, "Inspected child path: " + str(path)
        inspected.append(absolute)
    return original_stat(path, *args, **kwargs)
os.stat = root_stat
service = SourceService(SourceRepository(database), WindowsVolumeResolver())
row = service.register(sys.argv[2])
assert row["online"]
assert row["last_scan"] is None
row = service.refresh(row["source_id"])
assert row["online"], row["status_message"]
assert len(inspected) >= 2
service.set_enabled(row["source_id"], False)
service.rename(row["source_id"], "renamed")
service.remove(row["source_id"])
database.close()
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(media)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert original.read_bytes() == b"do not inspect or modify"
    assert list(nested.iterdir()) == [original]
