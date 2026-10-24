from pathlib import Path
import subprocess
import sys


def test_snapshot_finalization_uses_only_persisted_observations(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    original = media / "image.png"
    content = b"invalid image content; never decode"
    original.write_bytes(content)
    code = '''
import sys, os, hashlib
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from conftest import FakeVolumeResolver
from app.database.connection import Database
from app.database.repositories.sources import SourceRepository
from app.database.repositories.scans import ScanRepository
from app.services.sources import SourceService
from app.services.scanner import ScanService
from app.services.scan_policy import ScanPolicy
from app.services.snapshots import SnapshotService
database = Database(Path(sys.argv[1]) / "owned" / "db.sqlite")
database.initialize()
resolver = FakeVolumeResolver()
resolver.add(sys.argv[2])
sources = SourceService(SourceRepository(database), resolver)
row = sources.register(sys.argv[2])
scanner = ScanService(ScanRepository(database),sources,ScanPolicy())
scanner.snapshots.finalize = lambda scan_id: {"snapshot_id": None}
scan = scanner.scan(row["source_id"])
root = os.path.normcase(os.path.abspath(sys.argv[2]))
def within(value):
    if not isinstance(value,(str,bytes,os.PathLike)):
        return False
    value = os.path.normcase(os.path.abspath(os.fsdecode(value)))
    return value == root or value.startswith(root + os.sep)
def audit(event,args):
    if event in {"os.scandir","os.listdir","subprocess.Popen","os.system","os.startfile"} or event.startswith("socket."):
        raise AssertionError("Snapshot build attempted forbidden operation: " + event)
    if event in {"open","os.remove","os.rename","os.mkdir","os.rmdir","os.truncate","os.chmod","os.utime","os.link","os.symlink"} and any(within(value) for value in args):
        raise AssertionError("Snapshot build accessed media: " + event)
sys.addaudithook(audit)
for name in ("stat","lstat"):
    old = getattr(os,name)
    def guarded(path,*args,_old=old,**kwargs):
        if within(path):
            raise AssertionError("Snapshot build inspected source metadata")
        return _old(path,*args,**kwargs)
    setattr(os,name,guarded)
def forbidden(*args,**kwargs):
    raise AssertionError("No hashing or traversal during snapshot build")
hashlib.new = forbidden
hashlib.sha256 = forbidden
resolver.inspect = forbidden
resolver.mounts = forbidden
service = SnapshotService(database)
snapshot = service.finalize(scan["scan_id"])
assert snapshot["supported_media_count"] == 1
assert snapshot["eligible_for_reorganization_baseline"]
assert service.manifest(snapshot["snapshot_id"])[0]["file_size"] == len(b"invalid image content; never decode")
assert service.folders(snapshot["snapshot_id"])[0]["supported_media_count"] == 1
database.close()
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(media)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert original.read_bytes() == content and list(media.iterdir()) == [original]
