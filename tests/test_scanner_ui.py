from threading import Event, get_ident

from PySide6.QtCore import QTimer, Qt

from app.database.repositories.scans import ScanRepository
from app.services.scanner import ScanService, Filesystem
from app.services.scan_policy import ScanPolicy
from app.ui.source_page import SourcePage
from app.workers.manager import WorkerManager


def test_explicit_scan_cancel_worker_thread_and_ui_responsiveness(qtbot, source_setup, tmp_path):
    sources, resolver, database = source_setup
    root = tmp_path / "media"
    root.mkdir()
    (root / "image.png").write_bytes(b"sample")
    resolver.add(str(root))
    source = sources.register(str(root))
    entered, release = Event(), Event()
    threads = []
    class SlowFilesystem(Filesystem):
        def entries(self, path):
            threads.append(get_ident())
            entered.set()
            release.wait(3)
            return super().entries(path)
    scanner = ScanService(ScanRepository(database), sources, ScanPolicy(chunk_size=2), SlowFilesystem())
    manager = WorkerManager()
    page = SourcePage(sources, manager, scan_service=scanner)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: page._loaded and not page._busy)
    assert scanner.latest(source["source_id"]) is None  # Opening Sources does not scan.
    page.table.selectRow(0)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    try:
        qtbot.mouseClick(page.scan_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(entered.is_set)
        qtbot.waitUntil(lambda: len(ticks) >= 3)
        assert threads[0] != get_ident()
        assert page._scanning and page.scan_progress.maximum() == 0
        assert not page.remove_button.isEnabled()
        assert page.cancel_scan_button.isEnabled()
        qtbot.mouseClick(page.cancel_scan_button, Qt.MouseButton.LeftButton)
    finally:
        release.set()
    qtbot.waitUntil(lambda: not page._busy)
    timer.stop()
    assert "CANCELLED" in page.scan_message.text()
    assert scanner.latest(source["source_id"])["status"] == "CANCELLED"
    assert page.model.data(page.model.index(0, 7)) == "CANCELLED"
    manager.shutdown()


def test_completed_scan_and_saved_results_are_visible(qtbot, source_setup, tmp_path):
    sources, resolver, database = source_setup
    root = tmp_path / "media"
    root.mkdir()
    (root / "image.png").write_bytes(b"sample")
    resolver.add(str(root))
    source = sources.register(str(root))
    scanner = ScanService(ScanRepository(database), sources, ScanPolicy())
    manager = WorkerManager()
    page = SourcePage(sources, manager, scan_service=scanner)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: page._loaded and not page._busy)
    page.table.selectRow(0)
    qtbot.mouseClick(page.scan_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page._busy)
    assert "COMPLETED" in page.scan_message.text()
    assert "New: 1" in page.scan_message.text()
    page.table.selectRow(0)
    qtbot.mouseClick(page.last_scan_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page._busy)
    assert "COMPLETED" in page.scan_message.text()
    assert scanner.latest(source["source_id"])["reconciliation_performed"]
    manager.shutdown()


def test_closing_window_cancels_active_scan_cleanly(qtbot, source_setup, tmp_path):
    from app.config import AppConfig
    from app.ui.main_window import MainWindow
    sources, resolver, database = source_setup
    root = tmp_path / "media"
    root.mkdir()
    resolver.add(str(root))
    source = sources.register(str(root))
    entered, release = Event(), Event()
    class SlowFilesystem(Filesystem):
        def entries(self, path):
            entered.set()
            release.wait(3)
            return super().entries(path)
    scanner = ScanService(ScanRepository(database), sources, ScanPolicy(), SlowFilesystem())
    manager = WorkerManager()
    window = MainWindow(AppConfig(), manager, sources, scanner)
    qtbot.addWidget(window)
    window.show()
    window.navigation.setCurrentRow(1)
    page = window.source_page
    qtbot.waitUntil(lambda: not page._busy)
    page.table.selectRow(0)
    page.start_scan()
    qtbot.waitUntil(entered.is_set)
    QTimer.singleShot(20, release.set)
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert scanner.latest(source["source_id"])["status"] == "CANCELLED"
    assert manager.active_count == 0
