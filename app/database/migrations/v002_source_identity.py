"""M3 identity columns. Existing rows are preserved without inspecting any root."""
STATEMENTS = (
    "ALTER TABLE sources ADD COLUMN normalized_root_path TEXT",
    "ALTER TABLE sources ADD COLUMN volume_guid TEXT",
    "ALTER TABLE sources ADD COLUMN volume_serial TEXT",
    "ALTER TABLE sources ADD COLUMN volume_relative_path TEXT",
    "ALTER TABLE sources ADD COLUMN source_identity_key TEXT COLLATE NOCASE",
    "ALTER TABLE sources ADD COLUMN last_seen_at TEXT",
    "ALTER TABLE sources ADD COLUMN status_message TEXT",
    "UPDATE sources SET normalized_root_path = source_path_key(root_path)",
    """UPDATE sources SET source_identity_key = 'path:' || normalized_root_path
       WHERE normalized_root_path IS NOT NULL AND
       (SELECT COUNT(*) FROM sources other
        WHERE other.normalized_root_path = sources.normalized_root_path) = 1""",
    """UPDATE sources SET status_message = CASE WHEN source_identity_key IS NULL
       THEN 'Legacy path is invalid or overlaps a duplicate registration; review required.'
       ELSE 'Stable volume identity not yet resolved. Refresh Status to check.' END""",
    "CREATE UNIQUE INDEX idx_sources_identity ON sources(source_identity_key) WHERE source_identity_key IS NOT NULL",
    "CREATE INDEX idx_sources_normalized_root ON sources(normalized_root_path)",
)
