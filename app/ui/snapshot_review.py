"""Bounded database review UI; no filesystem access or repository calls."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Slot
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel,
    QPushButton, QTableView, QTabWidget, QVBoxLayout, QWidget)

from app.database.errors import DatabaseError
from app.services.scan_errors import ScanError


class ReviewModel(QAbstractTableModel):
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns, self.rows = columns, []

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section][0]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            key = self.columns[index.column()][1]
            value = self.rows[index.row()].get(key)
            if key == "folder_path" and value == "":
                return "."
            return "—" if value is None else str(value)
        return None


class SnapshotReview(QDialog):
    PAGE_SIZE = 100
    COLUMNS = {
        "history": (("Scan", "scan_id"), ("Snapshot", "snapshot_id"),
            ("Traversal", "effective_status"), ("Coverage", "coverage_status"),
            ("Future baseline eligible", "eligible_for_reorganization_baseline"),
            ("Started", "started_at"), ("Ended", "completed_at")),
        "manifest": (("Relative Path", "relative_path_at_scan"), ("Filename", "filename"),
            ("Type", "media_type"), ("Size (bytes)", "file_size"),
            ("Modified", "modified_time"), ("Observation", "observation_status")),
        "folders": (("Folder (direct contents)", "folder_path"),
            ("Supported media", "supported_media_count"), ("Supported-media bytes", "total_bytes")),
        "issues": (("Path", "path"), ("Category", "category"), ("Message", "user_message"),
            ("Technical details", "technical_message"), ("Time", "timestamp")),
    }

    def __init__(self, service, workers, source_id, parent=None):
        super().__init__(parent)
        self.service, self.workers, self.source_id = service, workers, source_id
        self.setWindowTitle("Scan History and Snapshot Review")
        self.resize(1100, 760)
        self._busy = False
        self._scan = None
        self._snapshot_id = None
        self._after = {kind: None if kind == "folders" else 0 for kind in self.COLUMNS}
        self._previous = {kind: [] for kind in self.COLUMNS}
        self.models, self.tables, self.pagers = {}, {}, {}
        layout = QVBoxLayout(self)
        self.message = QLabel("Historical database review only. No media is opened or reorganized.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        history = self._table_panel("history")
        history.setMaximumHeight(200)
        layout.addWidget(history)
        actions = QHBoxLayout()
        self.open_button = QPushButton("Open Selection")
        self.retry_button = QPushButton("Retry Snapshot Build")
        actions.addWidget(self.open_button)
        actions.addWidget(self.retry_button)
        layout.addLayout(actions)
        self.summary = QLabel("Select a scan to review its historical snapshot or operational issues.")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        self.eligibility = QLabel("No baseline selected")
        layout.addWidget(self.eligibility)
        tabs = QTabWidget()
        for kind, title in (("manifest", "Manifest"), ("folders", "Folders"), ("issues", "Warnings / Errors")):
            tabs.addTab(self._table_panel(kind), title)
        layout.addWidget(tabs)
        self.open_button.clicked.connect(self.open_selection)
        self.retry_button.clicked.connect(self.retry)
        self.tables["history"].selectionModel().selectionChanged.connect(self._buttons)
        self._buttons()
        self.request("history")

    def _table_panel(self, kind):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        table = QTableView()
        table.setAccessibleName(kind + " review table")
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        model = ReviewModel(self.COLUMNS[kind], self)
        table.setModel(model)
        layout.addWidget(table)
        controls = QHBoxLayout()
        previous, following = QPushButton("Previous"), QPushButton("Next")
        controls.addWidget(previous)
        controls.addWidget(following)
        layout.addLayout(controls)
        previous.clicked.connect(lambda: self.turn(kind, False))
        following.clicked.connect(lambda: self.turn(kind, True))
        self.models[kind], self.tables[kind] = model, table
        self.pagers[kind] = (previous, following)
        return panel

    def selected(self):
        rows = self.tables["history"].selectionModel().selectedRows()
        return self.models["history"].rows[rows[0].row()] if rows else None

    def _buttons(self, *args):
        row = self.selected()
        self.open_button.setEnabled(not self._busy and row is not None)
        self.retry_button.setEnabled(not self._busy and row is not None and row["effective_status"] == "COMPLETED" and row["snapshot_id"] is None and bool(row["observations_format"]))
        for kind, (previous, following) in self.pagers.items():
            allowed = not self._busy and (kind == "history" or self._scan is not None)
            if kind in {"folders", "manifest"}:
                allowed = allowed and self._snapshot_id is not None
            previous.setEnabled(allowed and bool(self._previous[kind]))
            following.setEnabled(allowed and len(self.models[kind].rows) == self.PAGE_SIZE)

    def _connect(self, signals):
        signals.result.connect(self._received)
        signals.failed.connect(self._failed)
        signals.finished.connect(self._finished)

    def request(self, kind, selected=None):
        if self._busy:
            return
        self._busy = True
        self._buttons()
        service, source_id = self.service, self.source_id
        scan, snapshot_id, after, limit = self._scan, self._snapshot_id, self._after.get(kind), self.PAGE_SIZE
        def work(context):
            try:
                if kind == "history":
                    result = service.history(source_id, after_id=after, limit=limit)
                elif kind in {"detail", "retry"}:
                    if kind == "retry":
                        snapshot = service.finalize(selected["scan_id"])
                        selected_row = dict(selected, snapshot_id=snapshot["snapshot_id"],
                            coverage_status=snapshot["coverage_status"],
                            eligible_for_reorganization_baseline=snapshot["eligible_for_reorganization_baseline"])
                    else:
                        selected_row = selected
                    sid = selected_row["snapshot_id"]
                    result = (selected_row, service.summary(sid) if sid else None,
                        service.manifest(sid, limit=limit) if sid else [],
                        service.folders(sid, limit=limit) if sid else [],
                        service.issues(selected_row["scan_id"], limit=limit))
                elif kind == "manifest":
                    result = service.manifest(snapshot_id, after_id=after, limit=limit)
                elif kind == "folders":
                    result = service.folders(snapshot_id, after_key=after, limit=limit)
                else:
                    result = service.issues(scan["scan_id"], after_id=after, limit=limit)
                return kind, result, None
            except (DatabaseError, ScanError) as exc:
                return kind, None, exc.user_message
        try:
            self.workers.submit(work, configure=self._connect)
        except RuntimeError as exc:
            self.message.setText(str(exc))
            self._finished()

    @Slot(object)
    def _received(self, payload):
        kind, value, error = payload
        if error:
            self.message.setText(error)
            return
        self.message.setText("Historical database review only. No media is opened or reorganized.")
        if kind in {"detail", "retry"}:
            row, summary, manifest, folders, issues = value
            self._scan, self._snapshot_id = row, row["snapshot_id"]
            if kind == "retry":
                self.models["history"].replace([
                    row if item["scan_id"] == row["scan_id"] else item
                    for item in self.models["history"].rows])
            for section, rows in (("manifest", manifest), ("folders", folders), ("issues", issues)):
                self._after[section] = None if section == "folders" else 0
                self._previous[section] = []
                self.models[section].replace(rows)
            if summary:
                self.summary.setText(
                    f"SNAP-{summary['snapshot_id']:06} / SCAN-{summary['scan_id']:06} — {summary['source_name']}\n"
                    f"Path at scan: {summary['source_root_at_scan']} | Identity: {summary['source_identity_key']}\n"
                    f"Start: {summary['started_at']} | Completion: {summary['completed_at']}\n"
                    f"Coverage: {summary['coverage_status']} | Directories observed: {summary['folder_count']} | "
                    f"Filesystem files examined: {summary['file_count']} | Unsupported files: {summary['unsupported_count']}\n"
                    f"Supported candidates examined: {summary['supported_candidates']} | "
                    f"Supported media in manifest: {summary['supported_media_count']} | Images: {summary['image_count']} | "
                    f"Videos: {summary['video_count']} | Supported-media bytes: {summary['total_bytes']}\n"
                    f"Skipped entries (excluding unsupported files): {summary['skipped_count']} | Errors: {summary['error_count']}")
                eligible = bool(summary["eligible_for_reorganization_baseline"])
                self.eligibility.setText("Eligible for a future reorganization baseline — review only" if eligible else "NOT eligible for a reorganization baseline: coverage is incomplete or warnings require review.")
                self.eligibility.setStyleSheet("color: #18723c; font-weight: 600" if eligible else "color: #9c4c00; font-weight: 600")
            else:
                reason = ("The scan is active or its owner could not be verified. Its lock is retained."
                    if row["effective_status"] == "RUNNING" else
                    "This traversal did not finish authoritatively. It has no finalized snapshot."
                    if row["effective_status"] in {"CANCELLED", "FAILED", "INTERRUPTED"} else
                    "Legacy scans without retained observations require a new explicit scan."
                    if not row["observations_format"] else
                    "Retained observations are available. Retry Snapshot Build to finalize without rescanning.")
                self.summary.setText(f"SCAN-{row['scan_id']:06}: {row['effective_status']} | Root: {row['root_path']}\n"
                    f"Started: {row['started_at']} | Ended: {row['completed_at'] or '—'}\n"
                    + (row["snapshot_error"] or reason))
                self.eligibility.setText("NOT eligible for a reorganization baseline — no finalized snapshot")
                self.eligibility.setStyleSheet("color: #9c4c00; font-weight: 600")
        else:
            self.models[kind].replace(value)
        for table in self.tables.values():
            table.resizeColumnsToContents()

    @Slot(object)
    def _failed(self, error):
        self.message.setText(error.user_message)

    @Slot()
    def _finished(self):
        self._busy = False
        self._buttons()

    def open_selection(self):
        row = self.selected()
        if row:
            self.request("detail", row)

    def retry(self):
        row = self.selected()
        if row:
            self.request("retry", row)

    def turn(self, kind, following):
        if self._busy:
            return
        if following and self.models[kind].rows:
            self._previous[kind].append(self._after[kind])
            key = {"history": "scan_id", "manifest": "manifest_entry_id", "folders": "folder_key", "issues": "issue_id"}[kind]
            self._after[kind] = self.models[kind].rows[-1][key]
        elif not following and self._previous[kind]:
            self._after[kind] = self._previous[kind].pop()
        else:
            return
        self.request(kind)
