import subprocess
import sys

import pytest

from app.database.connection import Database
from app.database.migrations import MIGRATIONS
from app.database.repositories import AssetRepository, SourceRepository
from app.services.snapshots import SnapshotService
from app.services.process_ownership import ProcessProbe
from test_scanner import scan_setup, fixture_asset


class Probe:
    def __init__(self, alive):
        self.alive = alive
    def is_alive(self, pid, token):
        return self.alive


def test_abandoned_scan_interrupted_without_reconciliation_and_releases_lock(scan_setup):
    scanner, _, source, _, database = scan_setup
    asset_id = fixture_asset(database, source, "unseen.png")
    scan_id = scanner.repository.claim(source, 1)
    with database.transaction() as connection:
        connection.execute("UPDATE scan_sessions SET files_seen=17,bytes_seen=42 WHERE scan_id=?", (scan_id,))
    recovery = SnapshotService(database, Probe(False))
    assert recovery.recover_interrupted() == 1
    assert recovery.recover_interrupted() == 0
    row = scanner.repository.get(scan_id)
    assert row["status"] == "INTERRUPTED" and row["files_seen"] == 17 and row["bytes_seen"] == 42
    assert not row["reconciliation_performed"] and not row["identity_verified"]
    assert not AssetRepository(database).get(asset_id)["missing"]
    assert not scanner.repository.active(source["source_id"])
    assert recovery.history(source["source_id"])[0]["snapshot_id"] is None
    assert recovery.issues(scan_id)[0]["category"] == "INTERRUPTED"
    scanner.sources.set_enabled(source["source_id"], False)
    scanner.sources.set_enabled(source["source_id"], True)
    fresh = scanner.scan(source["source_id"])
    assert fresh["status"] == "COMPLETED" and fresh["snapshot_id"]


@pytest.mark.parametrize("alive", [True, None])
def test_live_or_unknown_owner_never_recovered(scan_setup, alive):
    scanner, _, source, _, database = scan_setup
    scan_id = scanner.repository.claim(source, 1)
    recovery = SnapshotService(database, Probe(alive))
    assert recovery.recover_interrupted() == 0
    assert scanner.repository.get(scan_id)["status"] == "RUNNING"
    assert scanner.repository.active(source["source_id"])


def test_actual_current_process_is_not_recovered_and_pid_reuse_detected(scan_setup):
    scanner, _, source, _, database = scan_setup
    scan_id = scanner.repository.claim(source, 1)
    assert SnapshotService(database).recover_interrupted() == 0
    pid, token = ProcessProbe().current()
    assert ProcessProbe().is_alive(pid, token) is True
    assert ProcessProbe().is_alive(pid, "different creation") is False
    assert ProcessProbe().is_alive(pid, None) is None
    assert scanner.repository.get(scan_id)["status"] == "RUNNING"


def test_legacy_ownerless_running_scan_upgrades_and_interrupts_safely(tmp_path):
    database = Database(tmp_path / "legacy.db")
    database.initialize(MIGRATIONS[:3])
    source_id = SourceRepository(database).create(name="legacy", root_path="E:\\Media")
    with database.transaction() as connection:
        scan_id = connection.execute("INSERT INTO scan_sessions(source_id,root_path,status,files_seen) VALUES (?,?,'RUNNING',42)", (source_id, "E:\\Media")).lastrowid
    database.initialize()
    service = SnapshotService(database)
    assert service.recover_interrupted() == 1
    assert service.history(source_id)[0]["effective_status"] == "INTERRUPTED"
    assert service.history(source_id)[0]["files_seen"] == 42
    assert service.history(source_id)[0]["snapshot_id"] is None
    assert service.recover_interrupted() == 0
    SourceRepository(database).update(source_id, enabled=False)
    database.close()


def test_real_exited_process_recovery(scan_setup):
    scanner, _, source, _, database = scan_setup
    code = '''
import sys
from app.database.connection import Database
from app.database.repositories.scans import ScanRepository
from app.database.repositories.sources import SourceRepository
from pathlib import Path
database = Database(Path(sys.argv[1]))
database.initialize()
print(ScanRepository(database).claim(SourceRepository(database).get(int(sys.argv[2])),1))
database.close()
'''
    result = subprocess.run([sys.executable, "-c", code, str(database.path), str(source["source_id"])], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    scan_id = int(result.stdout.strip())
    assert SnapshotService(database).recover_interrupted() == 1
    assert scanner.repository.get(scan_id)["status"] == "INTERRUPTED"


def test_real_startup_classifies_abandoned_scan_idempotently(scan_setup, tmp_path):
    scanner, _, source, _, database = scan_setup
    scan_id = scanner.repository.claim(source, 1)
    with database.transaction() as connection:
        connection.execute("UPDATE scan_sessions SET owner_pid=2147483647,owner_start_token='old' WHERE scan_id=?", (scan_id,))
    config = tmp_path / "startup.toml"
    config.write_text("database_path='" + str(database.path) + "'", encoding="utf-8")
    code = '''
import sys
from PySide6.QtCore import QTimer
import app.main as entry
show = entry.MainWindow.show
def display(window):
    show(window)
    QTimer.singleShot(100,window.close)
entry.MainWindow.show = display
raise SystemExit(entry.main(["--config",sys.argv[1],"--runtime-root",sys.argv[2]]))
'''
    for _ in range(2):
        result = subprocess.run([sys.executable, "-c", code, str(config), str(tmp_path / "runtime")], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
    assert scanner.repository.get(scan_id)["status"] == "INTERRUPTED"
    assert len(scanner.issues(scan_id)) == 1
    assert not scanner.repository.active(source["source_id"])
