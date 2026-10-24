from threading import Event, get_ident

from PySide6.QtCore import QTimer, Qt

from app.ui.snapshot_review import SnapshotReview
from app.ui.source_page import SourcePage
from app.workers.manager import WorkerManager
from app.services.snapshots import SnapshotService
from test_scanner import scan_setup
from test_snapshot_large import seed_large_observations


def test_large_manifest_preview_is_bounded_and_responsive(scan_setup, qtbot, monkeypatch):
    scanner, _, source, _, _ = scan_setup
    scan_id = seed_large_observations(scanner, source)
    snapshot = scanner.snapshots.finalize(scan_id)
    manager = WorkerManager()
    review = SnapshotReview(scanner.snapshots, manager, source["source_id"])
    qtbot.addWidget(review)
    review.show()
    qtbot.waitUntil(lambda: not review._busy)
    assert review.models["history"].rowCount() == 1
    started, release = Event(), Event()
    threads = []
    original = scanner.snapshots.manifest
    def slow(*args, **kwargs):
        threads.append(get_ident())
        started.set()
        release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(scanner.snapshots, "manifest", slow)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    review.tables["history"].selectRow(0)
    try:
        qtbot.mouseClick(review.open_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(started.is_set)
        qtbot.waitUntil(lambda: len(ticks) >= 3)
        assert threads[0] != get_ident() and review._busy
    finally:
        release.set()
    qtbot.waitUntil(lambda: not review._busy)
    timer.stop()
    assert review.models["manifest"].rowCount() == 100
    assert review.models["folders"].rowCount() == 50
    assert "Eligible for a future" in review.eligibility.text()
    assert "Supported-media bytes" in review.summary.text()
    first = review.models["manifest"].rows[0]["manifest_entry_id"]
    qtbot.mouseClick(review.pagers["manifest"][1], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not review._busy)
    assert review.models["manifest"].rowCount() == 100
    assert review.models["manifest"].rows[0]["manifest_entry_id"] > first
    qtbot.mouseClick(review.pagers["manifest"][0], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not review._busy)
    assert review.models["manifest"].rows[0]["manifest_entry_id"] == first
    manager.shutdown()


def test_interrupted_history_is_not_presented_as_snapshot(scan_setup, qtbot):
    scanner, _, source, _, database = scan_setup
    scanner.repository.claim(source, 1)
    class Dead:
        def is_alive(self, *args):
            return False
    service = SnapshotService(database, Dead())
    manager = WorkerManager()
    review = SnapshotReview(service, manager, source["source_id"])
    qtbot.addWidget(review)
    review.show()
    qtbot.waitUntil(lambda: not review._busy)
    assert review.models["history"].data(review.models["history"].index(0, 2)) == "INTERRUPTED"
    review.tables["history"].selectRow(0)
    review.open_selection()
    qtbot.waitUntil(lambda: not review._busy)
    assert "NOT eligible" in review.eligibility.text()
    assert review.models["manifest"].rowCount() == 0
    assert review.models["issues"].rowCount() == 1
    assert not review.retry_button.isEnabled()
    manager.shutdown()


def test_sources_page_opens_review_without_scanning(scan_setup, qtbot):
    scanner, _, source, _, _ = scan_setup
    manager = WorkerManager()
    page = SourcePage(scanner.sources, manager, scan_service=scanner)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: not page._busy)
    page.table.selectRow(0)
    qtbot.mouseClick(page.history_button, Qt.MouseButton.LeftButton)
    review = page._history_dialog
    qtbot.waitUntil(lambda: not review._busy)
    assert review.models["history"].rowCount() == 0
    assert scanner.latest(source["source_id"]) is None
    review.close()
    manager.shutdown()


def test_history_pagination_and_retry_update_review(scan_setup, qtbot, monkeypatch):
    scanner, _, source, _, _ = scan_setup
    first = scanner.scan(source["source_id"])
    second = scanner.scan(source["source_id"])
    original = scanner.snapshots.finalize
    def failed(scan_id):
        raise RuntimeError("synthetic build failure")
    monkeypatch.setattr(scanner.snapshots, "finalize", failed)
    third = scanner.scan(source["source_id"])
    monkeypatch.setattr(scanner.snapshots, "finalize", original)
    class SmallReview(SnapshotReview):
        PAGE_SIZE = 2
    manager = WorkerManager()
    review = SmallReview(scanner.snapshots, manager, source["source_id"])
    qtbot.addWidget(review)
    review.show()
    qtbot.waitUntil(lambda: not review._busy)
    assert review.models["history"].rowCount() == 2
    qtbot.mouseClick(review.pagers["history"][1], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not review._busy)
    assert review.models["history"].rowCount() == 1
    review.tables["history"].selectRow(0)
    assert review.retry_button.isEnabled()
    qtbot.mouseClick(review.retry_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not review._busy)
    assert review.models["history"].rows[0]["snapshot_id"] is not None
    assert "Eligible for a future" in review.eligibility.text()
    qtbot.mouseClick(review.pagers["history"][0], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not review._busy)
    assert review.models["history"].rows[0]["scan_id"] == first["scan_id"]
    manager.shutdown()
