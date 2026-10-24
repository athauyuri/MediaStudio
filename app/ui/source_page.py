"""Source presentation; every service/DB/volume operation runs in a worker."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Slot
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QMessageBox, QPushButton, QTableView, QVBoxLayout, QWidget,
    QProgressBar, QPlainTextEdit)
from threading import Event

from app.database.errors import DatabaseError
from app.services.source_errors import SourceError
from app.services.sources import SourceService
from app.services.scan_errors import ScanError
from app.ui.snapshot_review import SnapshotReview


class SourceTableModel(QAbstractTableModel):
    HEADERS = ("Name", "Current Path", "Status", "Volume / Drive", "Enabled", "Last Seen", "Last Scan", "Scan State")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.get("status_message") or row.get("volume_guid") or row["root_path"]
        if role == Qt.ItemDataRole.DisplayRole:
            return (row["name"], row["root_path"], SourceService.status(row),
                    row.get("volume_guid") or row.get("current_mount") or "Unresolved",
                    "Yes" if row["enabled"] else "No", row.get("last_seen_at") or "—",
                    row.get("last_scan") or "Not yet scanned",
                    row.get("scan_status") or "Not Scanned")[index.column()]
        return None


class SourcePage(QWidget):
    PAGE_SIZE = 100

    def __init__(self, service, workers, parent=None, *, scan_service=None, snapshot_service=None):
        super().__init__(parent)
        self.service, self.workers = service, workers
        self.scan_service = scan_service
        self.snapshot_service = snapshot_service or (scan_service.snapshots if scan_service else None)
        self._scanning = False
        self._scan_cancel = Event()
        self._busy = False
        self._loaded = False
        self._after = 0
        self._history = []
        self._retry = None
        layout = QVBoxLayout(self)
        title = QLabel("Sources")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(QLabel("Registering a source does not scan it."))
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add Source")
        self.refresh_button = QPushButton("Refresh Status")
        self.enable_button = QPushButton("Enable / Disable")
        self.rename_button = QPushButton("Rename Display Name")
        self.remove_button = QPushButton("Remove Source")
        self._actions = [self.add_button, self.refresh_button, self.enable_button,
                         self.rename_button, self.remove_button]
        for button in self._actions:
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.model = SourceTableModel(self)
        self.table = QTableView()
        self.table.setAccessibleName("Registered sources")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        scan_buttons = QHBoxLayout()
        self.scan_button = QPushButton("Scan Source")
        self.cancel_scan_button = QPushButton("Cancel Scan")
        self.last_scan_button = QPushButton("Show Last Scan")
        self.more_issues_button = QPushButton("More Scan Issues")
        self.history_button = QPushButton("Review Scan History")
        for button in (self.scan_button, self.cancel_scan_button, self.last_scan_button, self.more_issues_button, self.history_button):
            scan_buttons.addWidget(button)
        layout.addLayout(scan_buttons)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(0)
        self.scan_progress.setTextVisible(False)
        layout.addWidget(self.scan_progress)
        self.scan_message = QLabel("Scanning is explicit and reads directory entries/filesystem metadata only.")
        self.scan_message.setWordWrap(True)
        layout.addWidget(self.scan_message)
        self.scan_results = QPlainTextEdit()
        self.scan_results.setReadOnly(True)
        self.scan_results.setMaximumHeight(140)
        self.scan_results.setAccessibleName("Scan results and issues")
        layout.addWidget(self.scan_results)
        self._result_scan_id = None
        self._issue_after = 0
        footer = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        self.message = QLabel("Select Refresh Status to check registered roots. No scan will run.")
        self.message.setWordWrap(True)
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        footer.addWidget(self.message, 1)
        layout.addLayout(footer)
        self.add_button.clicked.connect(self._choose_folder)
        self.refresh_button.clicked.connect(self.refresh_status)
        self.enable_button.clicked.connect(self._toggle)
        self.rename_button.clicked.connect(self._rename)
        self.remove_button.clicked.connect(self._remove)
        self.previous_button.clicked.connect(self._previous)
        self.next_button.clicked.connect(self._next)
        self.scan_button.clicked.connect(self.start_scan)
        self.cancel_scan_button.clicked.connect(self.cancel_scan)
        self.last_scan_button.clicked.connect(self.show_last_scan)
        self.more_issues_button.clicked.connect(self.more_issues)
        self.history_button.clicked.connect(self.review_history)
        self.table.selectionModel().selectionChanged.connect(self._buttons)
        self._buttons()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.load()

    def selected(self):
        indexes = self.table.selectionModel().selectedRows()
        return self.model.rows[indexes[0].row()] if indexes else None

    def _buttons(self, *args):
        available = self.service is not None and not self._busy
        self.add_button.setEnabled(available)
        self.refresh_button.setEnabled(available)
        for button in (self.enable_button, self.rename_button, self.remove_button):
            button.setEnabled(available and self.selected() is not None)
        self.previous_button.setEnabled(available and bool(self._history))
        self.next_button.setEnabled(available and len(self.model.rows) == self.PAGE_SIZE)
        selected = self.selected()
        self.scan_button.setEnabled(available and self.scan_service is not None and selected is not None and bool(selected["enabled"]))
        self.last_scan_button.setEnabled(available and self.scan_service is not None and selected is not None)
        self.cancel_scan_button.setEnabled(self._scanning)
        self.more_issues_button.setEnabled(available and self._result_scan_id is not None and self._issue_after > 0)
        self.history_button.setEnabled(available and self.snapshot_service is not None and selected is not None)

    def _connect(self, signals):
        signals.result.connect(self._completed)
        signals.failed.connect(self._failed)
        signals.finished.connect(self._finished)

    def _run(self, operation=None):
        if self._busy or self.service is None:
            return
        service, after = self.service, self._after
        self._busy = True
        self.message.setText("Working… No scan is running.")
        self._buttons()
        def work(context):
            try:
                context.checkpoint()
                changed = operation(context) if operation else None
                context.checkpoint()
                return (service.list_sources(after_id=after, limit=self.PAGE_SIZE), changed, None)
            except (SourceError, DatabaseError) as exc:
                return (None, None, exc)
        try:
            self.workers.submit(work, configure=self._connect)
        except RuntimeError as exc:
            self._busy = False
            self.message.setText(str(exc))
            self._buttons()

    @Slot(object)
    def _completed(self, result):
        rows, changed, error = result
        if error is not None:
            self.message.setText(error.user_message)
            if isinstance(error, SourceError) and error.error_code == "NESTED_CONFIRMATION" and self._retry:
                retry = self._retry
                # Run after the task's finished signal clears busy state.
                QTimer.singleShot(0, lambda: self._confirm_nested(error.user_message, retry))
            return
        self.model.replace(rows)
        self.table.resizeColumnsToContents()
        self.message.setText((changed or {}).get("status_message") or
                             f"{len(rows)} sources on this page. Registering a source does not scan it.")

    def _confirm_nested(self, message, retry):
        if QMessageBox.question(self, "Overlapping sources", message) == QMessageBox.StandardButton.Yes:
            retry()

    @Slot(object)
    def _failed(self, error):
        self.message.setText(error.user_message)

    @Slot()
    def _finished(self):
        self._busy = False
        self._buttons()

    def load(self):
        self._run()

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a media source folder", "",
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks)
        if path:
            self.register_path(path)

    def register_path(self, path, *, allow_nested=False):
        service = self.service
        self._retry = lambda: self.register_path(path, allow_nested=True)
        self._run(lambda context: service.register(path, allow_nested=allow_nested))

    def refresh_status(self):
        ids = [row["source_id"] for row in self.model.rows]
        service = self.service
        def refresh(context):
            last = None
            for source_id in ids:
                context.checkpoint()
                last = service.refresh(source_id)
            return last
        self._run(refresh)

    def _toggle(self):
        row = self.selected()
        if row:
            service = self.service
            self._run(lambda context: service.set_enabled(row["source_id"], not row["enabled"]))

    def _rename(self):
        row = self.selected()
        if row:
            name, accepted = QInputDialog.getText(self, "Rename Display Name", "Name:", text=row["name"])
            if accepted:
                service = self.service
                self._run(lambda context: service.rename(row["source_id"], name))

    def _remove(self):
        row = self.selected()
        if row and QMessageBox.question(self, "Remove Source",
            f"Remove the registration for {row['name']}?\nThe folder and files will remain untouched.") == QMessageBox.StandardButton.Yes:
            service = self.service
            self._run(lambda context: service.remove(row["source_id"]))

    def _next(self):
        if self.model.rows and not self._busy:
            self._history.append(self._after)
            self._after = self.model.rows[-1]["source_id"]
            self.load()

    def _previous(self):
        if self._history and not self._busy:
            self._after = self._history.pop()
            self.load()

    def _connect_scan(self, signals):
        signals.update.connect(self._scan_update)
        signals.result.connect(self._scan_completed)
        signals.failed.connect(self._failed)
        signals.finished.connect(self._scan_finished)

    @Slot(object)
    def _scan_update(self, value):
        self.scan_message.setText(self._summary(value))

    @staticmethod
    def _summary(value):
        return (f"{value['status']} | {value.get('current_folder') or value.get('root_path') or ''}\n"
            f"Directories: {value['directories_seen']} | Files examined: {value['files_seen']} | "
            f"Supported: {value['supported_files']} | New: {value['files_indexed']} | "
            f"Updated: {value['files_updated']} | Skipped: {value['files_skipped']} | "
            f"Errors: {value['errors']} | Bytes observed: {value['bytes_seen']}")

    def _scan_request(self, source_id, *, run=False, more=False):
        if self._busy or self.scan_service is None:
            return
        scanner, sources, after = self.scan_service, self.service, self._after
        scan_id, issue_after = self._result_scan_id, self._issue_after if more else 0
        self._busy = True
        self._scanning = run
        self._scan_cancel = Event()
        cancel = self._scan_cancel
        self._buttons()
        if run:
            self.scan_progress.setRange(0, 0)
            self.scan_message.setText("Checking source eligibility… No percentage is assumed.")
        def work(context):
            try:
                result = scanner.scan(source_id, context=context, cancel=cancel, progress=context.notify) if run else scanner.result(scan_id) if more else scanner.latest(source_id)
                issues = scanner.issues(result["scan_id"], after_id=issue_after) if result else []
                return (sources.list_sources(after_id=after, limit=self.PAGE_SIZE), result, issues, None)
            except (ScanError, SourceError, DatabaseError) as exc:
                return (None, None, [], exc)
        try:
            self.workers.submit(work, configure=self._connect_scan)
        except RuntimeError as exc:
            self.message.setText(str(exc))
            self._scan_finished()

    def start_scan(self):
        row = self.selected()
        if row:
            self._scan_request(row["source_id"], run=True)

    def cancel_scan(self):
        self._scan_cancel.set()
        self.scan_message.setText("Cancellation requested. Finishing the current bounded operation safely…")
        self.cancel_scan_button.setEnabled(False)

    def show_last_scan(self):
        row = self.selected()
        if row:
            self._scan_request(row["source_id"])

    def more_issues(self):
        self._scan_request(None, more=True)

    def review_history(self):
        row = self.selected()
        if row and self.snapshot_service:
            self._history_dialog = SnapshotReview(self.snapshot_service, self.workers, row["source_id"], self)
            self._history_dialog.open()

    @Slot(object)
    def _scan_completed(self, payload):
        rows, result, issues, error = payload
        if error:
            self.scan_message.setText(error.user_message)
            return
        self.model.replace(rows)
        if result is None:
            self.scan_message.setText("This source has not been scanned.")
            return
        self._result_scan_id = result["scan_id"]
        self._issue_after = issues[-1]["issue_id"] if len(issues) == 100 else 0
        reconciliation = "performed" if result["reconciliation_performed"] else "not performed (incomplete coverage, cancellation, or failure)"
        self.scan_message.setText(self._summary(result) + f"\nMissing reconciliation: {reconciliation}. Missing marked: {result['missing_marked']}.")
        if result.get("snapshot_error"):
            self.scan_message.setText(self.scan_message.text() + "\nSnapshot build failed: " + result["snapshot_error"] + " Review Scan History to retry without rescanning.")
        self.scan_results.setPlainText("\n\n".join(
            f"{issue['timestamp']} [{issue['category']}] {issue['path']}\n{issue['user_message']}\n{issue['technical_message']}"
            for issue in issues) or "No scan issues recorded.")

    @Slot()
    def _scan_finished(self):
        self._busy = False
        self._scanning = False
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(1)
        self._buttons()
