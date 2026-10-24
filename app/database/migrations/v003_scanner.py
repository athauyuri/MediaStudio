"""M4 operational state only; immutable manifests belong to M5."""
STATEMENTS = (
    """CREATE TABLE scan_sessions (
        scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
        root_path TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','CANCELLED','FAILED')),
        started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        completed_at TEXT,
        current_folder TEXT,
        files_seen INTEGER NOT NULL DEFAULT 0,
        supported_files INTEGER NOT NULL DEFAULT 0,
        directories_seen INTEGER NOT NULL DEFAULT 0,
        files_indexed INTEGER NOT NULL DEFAULT 0,
        files_updated INTEGER NOT NULL DEFAULT 0,
        files_skipped INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        bytes_seen INTEGER NOT NULL DEFAULT 0,
        coverage_complete INTEGER NOT NULL DEFAULT 1,
        reconciliation_performed INTEGER NOT NULL DEFAULT 0,
        missing_marked INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE UNIQUE INDEX idx_scan_running_source ON scan_sessions(source_id) WHERE status='RUNNING'",
    "CREATE INDEX idx_scan_source_history ON scan_sessions(source_id, scan_id)",
    """CREATE TABLE scan_issues (
        issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id INTEGER NOT NULL REFERENCES scan_sessions(scan_id) ON DELETE CASCADE,
        path TEXT NOT NULL, category TEXT NOT NULL, user_message TEXT NOT NULL,
        technical_message TEXT NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""",
    "CREATE INDEX idx_scan_issues_session ON scan_issues(scan_id, issue_id)",
    """CREATE TABLE scan_pending_directories (
        scan_id INTEGER NOT NULL REFERENCES scan_sessions(scan_id) ON DELETE CASCADE,
        path_key TEXT NOT NULL, path TEXT NOT NULL,
        PRIMARY KEY(scan_id, path_key)
    )""",
    "ALTER TABLE assets ADD COLUMN normalized_absolute_path TEXT",
    "ALTER TABLE assets ADD COLUMN normalized_relative_path TEXT",
    "ALTER TABLE assets ADD COLUMN last_seen_scan_id INTEGER REFERENCES scan_sessions(scan_id) ON DELETE SET NULL",
    "UPDATE assets SET normalized_absolute_path=source_path_key(absolute_path), normalized_relative_path=source_relative_key(relative_path)",
    "CREATE INDEX idx_assets_normalized_absolute ON assets(normalized_absolute_path)",
    "CREATE INDEX idx_assets_source_relative ON assets(source_id, normalized_relative_path)",
    "CREATE INDEX idx_assets_scan_seen ON assets(source_id, last_seen_scan_id)",
    """CREATE TRIGGER source_update_during_scan BEFORE UPDATE ON sources
        WHEN EXISTS(SELECT 1 FROM scan_sessions WHERE source_id=OLD.source_id AND status='RUNNING')
        BEGIN SELECT RAISE(ABORT,'SOURCE_SCAN_ACTIVE'); END""",
    """CREATE TRIGGER source_delete_during_scan BEFORE DELETE ON sources
        WHEN EXISTS(SELECT 1 FROM scan_sessions WHERE source_id=OLD.source_id AND status='RUNNING')
        BEGIN SELECT RAISE(ABORT,'SOURCE_SCAN_ACTIVE'); END""",
)
