from pathlib import Path
import subprocess
import sys


def test_scan_audit_enumerates_but_never_opens_or_changes_media(tmp_path):
    media = tmp_path / "media"
    child = media / "子目录"
    child.mkdir(parents=True)
    fixtures = {media / "image.PNG": b"intentionally invalid image bytes",
                child / "video.MP4": b"intentionally invalid video bytes",
                child / "ignored.txt": b"preserve this too"}
    for path, value in fixtures.items():
        path.write_bytes(value)
    code = '''
import os, sys, hashlib
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from conftest import FakeVolumeResolver
from app.database.connection import Database
from app.database.repositories.sources import SourceRepository
from app.database.repositories.scans import ScanRepository
from app.services.sources import SourceService
from app.services.scanner import ScanService
from app.services.scan_policy import ScanPolicy
database = Database(Path(sys.argv[1]) / "owned" / "studio.db")
database.initialize()
resolver = FakeVolumeResolver()
resolver.add(sys.argv[2])
sources = SourceService(SourceRepository(database), resolver)
source = sources.register(sys.argv[2])
scanner = ScanService(ScanRepository(database), sources, ScanPolicy(chunk_size=2))
root = os.path.normcase(os.path.abspath(sys.argv[2]))
enumerations = []
def within(value):
    if not isinstance(value, (str, bytes, os.PathLike)):
        return False
    value = os.path.normcase(os.path.abspath(os.fsdecode(value)))
    return value == root or value.startswith(root + os.sep)
def audit(event, args):
    if event == "os.scandir" and within(args[0]):
        enumerations.append(args[0])
    if event in {"open", "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.truncate", "os.utime", "os.chmod", "os.link", "os.symlink"}:
        if any(within(value) for value in args):
            raise AssertionError("Forbidden media access/mutation: " + event)
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.startfile"}:
        raise AssertionError("Forbidden external processing/network: " + event)
sys.addaudithook(audit)
def forbidden_hash(*args, **kwargs):
    raise AssertionError("Hashing is not allowed during a scan")
hashlib.new = forbidden_hash
hashlib.sha256 = forbidden_hash
result = scanner.scan(source["source_id"])
assert result["status"] == "COMPLETED", scanner.issues(result["scan_id"])
assert result["files_indexed"] == 2
assert len(enumerations) == 2
database.close()
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(media)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert set(media.rglob("*")) == set(fixtures) | {child}
    for path, value in fixtures.items():
        assert path.read_bytes() == value
