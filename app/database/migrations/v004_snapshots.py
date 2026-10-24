"""M5 immutable historical observations/snapshots; no filesystem access."""
STATEMENTS = (
    "ALTER TABLE scan_sessions ADD COLUMN owner_pid INTEGER",
    "ALTER TABLE scan_sessions ADD COLUMN owner_start_token TEXT",
    "ALTER TABLE scan_sessions ADD COLUMN source_identity_at_scan TEXT",
    "ALTER TABLE scan_sessions ADD COLUMN source_name_at_scan TEXT",
    "ALTER TABLE scan_sessions ADD COLUMN mount_at_scan TEXT",
    "ALTER TABLE scan_sessions ADD COLUMN observations_format INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scan_sessions ADD COLUMN observations_sealed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scan_sessions ADD COLUMN identity_verified INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scan_sessions ADD COLUMN unsupported_files INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scan_sessions ADD COLUMN interrupted_at TEXT",
    "ALTER TABLE scan_sessions ADD COLUMN snapshot_error TEXT",
    """CREATE TABLE scan_observations (
        observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id INTEGER NOT NULL REFERENCES scan_sessions(scan_id) ON DELETE CASCADE,
        asset_id INTEGER,
        absolute_path_at_scan TEXT NOT NULL, relative_path_at_scan TEXT NOT NULL,
        filename TEXT NOT NULL, extension TEXT NOT NULL,
        media_type TEXT NOT NULL CHECK(media_type IN ('image','video')),
        file_size INTEGER NOT NULL CHECK(file_size>=0),
        created_time TEXT NOT NULL, modified_time TEXT NOT NULL,
        comparison_key TEXT NOT NULL, folder_key TEXT NOT NULL, folder_path TEXT NOT NULL,
        observation_status TEXT NOT NULL CHECK(observation_status IN ('OBSERVED','PATH_CONFLICT'))
    )""",
    "CREATE INDEX idx_observations_scan ON scan_observations(scan_id,observation_id)",
    """CREATE TABLE scan_snapshots (
        snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id INTEGER NOT NULL UNIQUE REFERENCES scan_sessions(scan_id) ON DELETE RESTRICT,
        source_id INTEGER NOT NULL, source_name TEXT NOT NULL, source_identity_key TEXT NOT NULL,
        source_root_at_scan TEXT NOT NULL, mount_at_scan TEXT,
        started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('BUILDING','FINALIZED')),
        coverage_status TEXT NOT NULL CHECK(coverage_status IN ('COMPLETE','COMPLETE_WITH_WARNINGS','INCOMPLETE')),
        eligible_for_reorganization_baseline INTEGER NOT NULL CHECK(eligible_for_reorganization_baseline IN (0,1)),
        folder_count INTEGER NOT NULL, file_count INTEGER NOT NULL,
        unsupported_count INTEGER NOT NULL, supported_media_count INTEGER NOT NULL,
        supported_candidates INTEGER NOT NULL, image_count INTEGER NOT NULL,
        video_count INTEGER NOT NULL, total_bytes INTEGER NOT NULL,
        skipped_count INTEGER NOT NULL, error_count INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""",
    "CREATE INDEX idx_snapshots_source ON scan_snapshots(source_id,snapshot_id)",
    """CREATE TABLE scan_manifest_entries (
        manifest_entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL REFERENCES scan_snapshots(snapshot_id) ON DELETE RESTRICT,
        asset_id INTEGER,
        absolute_path_at_scan TEXT NOT NULL, relative_path_at_scan TEXT NOT NULL,
        filename TEXT NOT NULL, extension TEXT NOT NULL, media_type TEXT NOT NULL,
        file_size INTEGER NOT NULL, created_time TEXT NOT NULL, modified_time TEXT NOT NULL,
        comparison_key TEXT NOT NULL, folder_key TEXT NOT NULL, folder_path TEXT NOT NULL,
        observation_status TEXT NOT NULL
    )""",
    "CREATE INDEX idx_manifest_snapshot ON scan_manifest_entries(snapshot_id,manifest_entry_id)",
    "CREATE INDEX idx_manifest_folder ON scan_manifest_entries(snapshot_id,folder_key,manifest_entry_id)",
    "CREATE INDEX idx_manifest_asset ON scan_manifest_entries(asset_id) WHERE asset_id IS NOT NULL",
    """CREATE VIEW scan_history AS SELECT scan_sessions.*,
        CASE WHEN interrupted_at IS NOT NULL THEN 'INTERRUPTED' ELSE status END AS effective_status
        FROM scan_sessions""",
    """CREATE TRIGGER snapshot_no_update BEFORE UPDATE ON scan_snapshots WHEN OLD.state='FINALIZED'
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SNAPSHOT'); END""",
    """CREATE TRIGGER snapshot_no_delete BEFORE DELETE ON scan_snapshots
        BEGIN SELECT RAISE(ABORT,'SNAPSHOT_RETENTION_REQUIRED'); END""",
    """CREATE TRIGGER manifest_no_insert BEFORE INSERT ON scan_manifest_entries
        WHEN (SELECT state FROM scan_snapshots WHERE snapshot_id=NEW.snapshot_id)!='BUILDING'
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_MANIFEST'); END""",
    """CREATE TRIGGER manifest_no_update BEFORE UPDATE ON scan_manifest_entries
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_MANIFEST'); END""",
    """CREATE TRIGGER manifest_no_delete BEFORE DELETE ON scan_manifest_entries
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_MANIFEST'); END""",
    """CREATE TRIGGER observation_no_insert BEFORE INSERT ON scan_observations
        WHEN (SELECT observations_sealed FROM scan_sessions WHERE scan_id=NEW.scan_id)=1
        BEGIN SELECT RAISE(ABORT,'SEALED_OBSERVATIONS'); END""",
    """CREATE TRIGGER observation_no_update BEFORE UPDATE ON scan_observations
        WHEN (SELECT observations_sealed FROM scan_sessions WHERE scan_id=OLD.scan_id)=1
          OR (SELECT observations_sealed FROM scan_sessions WHERE scan_id=NEW.scan_id)=1
        BEGIN SELECT RAISE(ABORT,'SEALED_OBSERVATIONS'); END""",
    """CREATE TRIGGER observation_no_delete BEFORE DELETE ON scan_observations
        WHEN EXISTS(SELECT 1 FROM scan_snapshots WHERE scan_id=OLD.scan_id)
        BEGIN SELECT RAISE(ABORT,'SNAPSHOT_HISTORY_RETAINED'); END""",
    """CREATE TRIGGER snapshot_scan_no_update BEFORE UPDATE ON scan_sessions
        WHEN EXISTS(SELECT 1 FROM scan_snapshots WHERE scan_id=OLD.scan_id AND state='FINALIZED')
        BEGIN SELECT RAISE(ABORT,'SNAPSHOT_HISTORY_RETAINED'); END""",
    """CREATE TRIGGER snapshot_scan_no_delete BEFORE DELETE ON scan_sessions
        WHEN EXISTS(SELECT 1 FROM scan_snapshots WHERE scan_id=OLD.scan_id)
        BEGIN SELECT RAISE(ABORT,'SNAPSHOT_HISTORY_RETAINED'); END""",
) + tuple(
    f"""CREATE TRIGGER snapshot_issue_no_{operation.lower()} BEFORE {operation} ON scan_issues
        WHEN EXISTS(SELECT 1 FROM scan_snapshots WHERE ({condition}) AND state='FINALIZED')
        BEGIN SELECT RAISE(ABORT,'SNAPSHOT_HISTORY_RETAINED'); END"""
    for operation, condition in (("INSERT", "scan_id=NEW.scan_id"),
        ("UPDATE", "scan_id=OLD.scan_id OR scan_id=NEW.scan_id"), ("DELETE", "scan_id=OLD.scan_id"))
)
