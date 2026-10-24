from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from app.config import AppConfig
from app.workers.manager import WorkerManager
from app.ui.source_page import SourcePage

NAVIGATION = ("Library", "Sources", "Collections", "Reorganize", "Rename", "Duplicates",
              "Generate", "Batch Jobs", "Activity", "Settings")


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, workers: WorkerManager, source_service=None, scan_service=None, snapshot_service=None):
        super().__init__()
        self.workers = workers
        self.setWindowTitle(config.title)
        self.resize(config.width, config.height)
        self.setMinimumSize(640, 480)
        self.navigation = QListWidget()
        self.navigation.setAccessibleName("Main navigation")
        self.navigation.addItems(NAVIGATION)
        self.navigation.setMaximumWidth(200)
        self.pages = QStackedWidget()
        for name in NAVIGATION:
            if name == "Sources":
                self.source_page = SourcePage(source_service, workers, scan_service=scan_service, snapshot_service=snapshot_service)
                self.pages.addWidget(self.source_page)
                continue
            page = QWidget()
            layout = QVBoxLayout(page)
            title = QLabel(name)
            title.setStyleSheet("font-size: 24px; font-weight: 600;")
            layout.addWidget(title)
            message = QLabel("Application skeleton ready. This feature is planned for a later milestone.")
            message.setWordWrap(True)
            layout.addWidget(message)
            layout.addStretch()
            self.pages.addWidget(page)
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.statusBar().showMessage("M5 — Scan snapshots ready")
        self._close_timer = QTimer(self)
        self._close_timer.setInterval(50)
        self._close_timer.timeout.connect(self._finish_close)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.workers.shutdown()
        if self.workers.active_count or self.workers.pool.activeThreadCount():
            event.ignore()
            self.statusBar().showMessage("Stopping background tasks…")
            self._close_timer.start()
        else:
            self._close_timer.stop()
            event.accept()

    def _finish_close(self) -> None:
        if not self.workers.active_count and not self.workers.pool.activeThreadCount():
            self.close()
