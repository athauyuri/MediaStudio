"""Database-only snapshot finalization and bounded historical queries."""
from app.services.scan_errors import ScanError


def page_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Page size must be between 1 and 1000")


class SnapshotRepository:
    def __init__(self, database):
        self.database = database

    def finalize(self, scan_id):
        with self.database.transaction() as connection:
            existing = connection.execute("SELECT * FROM scan_snapshots WHERE scan_id=? AND state='FINALIZED'", (scan_id,)).fetchone()
            if existing:
                return dict(existing)
            scan = connection.execute("SELECT * FROM scan_sessions WHERE scan_id=?", (scan_id,)).fetchone()
            if not scan or scan["status"] != "COMPLETED" or scan["interrupted_at"] is not None:
                raise ScanError("SNAPSHOT_NOT_ALLOWED", "Only a successfully completed traversal can create a snapshot.")
            if not scan["observations_format"] or not scan["observations_sealed"] or not scan["identity_verified"] or not scan["source_identity_at_scan"]:
                raise ScanError("OBSERVATIONS_UNAVAILABLE", "This scan has no authoritative retained observations. Run a new explicit scan; historical values cannot be reconstructed from current assets.")
            counts = connection.execute("""SELECT COUNT(*) AS media,
                COALESCE(SUM(media_type='image'),0) AS images,
                COALESCE(SUM(media_type='video'),0) AS videos,
                COALESCE(SUM(file_size),0) AS bytes,
                COALESCE(SUM(observation_status!='OBSERVED'),0) AS conflicts
                FROM scan_observations WHERE scan_id=?""", (scan_id,)).fetchone()
            skipped = max(0, scan["files_skipped"] - scan["unsupported_files"])
            complete = bool(scan["coverage_complete"]) and not counts["conflicts"]
            coverage = "COMPLETE" if complete and not skipped and not scan["errors"] else "COMPLETE_WITH_WARNINGS" if complete else "INCOMPLETE"
            eligible = coverage == "COMPLETE"
            snapshot_id = connection.execute("""INSERT INTO scan_snapshots(
                scan_id,source_id,source_name,source_identity_key,source_root_at_scan,mount_at_scan,
                started_at,completed_at,state,coverage_status,eligible_for_reorganization_baseline,
                folder_count,file_count,unsupported_count,supported_media_count,supported_candidates,
                image_count,video_count,total_bytes,skipped_count,error_count)
                VALUES (?,?,?,?,?,?,?,?,'BUILDING',?,?,?,?,?,?,?,?,?,?,?,?)""",
                (scan_id, scan["source_id"], scan["source_name_at_scan"], scan["source_identity_at_scan"],
                 scan["root_path"], scan["mount_at_scan"], scan["started_at"], scan["completed_at"],
                 coverage, eligible, scan["directories_seen"], scan["files_seen"], scan["unsupported_files"],
                 counts["media"], scan["supported_files"], counts["images"], counts["videos"],
                 counts["bytes"], skipped, scan["errors"])).lastrowid
            self._populate(connection, snapshot_id, scan_id)
            connection.execute("UPDATE scan_sessions SET snapshot_error=NULL WHERE scan_id=?", (scan_id,))
            connection.execute("UPDATE scan_snapshots SET state='FINALIZED' WHERE snapshot_id=?", (snapshot_id,))
            return dict(connection.execute("SELECT * FROM scan_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone())

    @staticmethod
    def _populate(connection, snapshot_id, scan_id):
        connection.execute("""INSERT INTO scan_manifest_entries(snapshot_id,asset_id,
            absolute_path_at_scan,relative_path_at_scan,filename,extension,media_type,file_size,
            created_time,modified_time,comparison_key,folder_key,folder_path,observation_status)
            SELECT ?,asset_id,absolute_path_at_scan,relative_path_at_scan,filename,extension,
            media_type,file_size,created_time,modified_time,comparison_key,folder_key,folder_path,
            observation_status FROM scan_observations WHERE scan_id=? ORDER BY observation_id""", (snapshot_id, scan_id))

    def record_error(self, scan_id, message):
        with self.database.transaction() as connection:
            connection.execute("UPDATE scan_sessions SET snapshot_error=? WHERE scan_id=?", (message, scan_id))

    def get(self, snapshot_id):
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM scan_snapshots WHERE snapshot_id=? AND state='FINALIZED'", (snapshot_id,)).fetchone()
            if not row:
                raise ScanError("SNAPSHOT_MISSING", "The finalized snapshot was not found.")
            return dict(row)

    def history(self, source_id, *, after_id=0, limit=100):
        page_limit(limit)
        with self.database.session() as connection:
            return [dict(row) for row in connection.execute("""SELECT h.*,s.snapshot_id,
                s.coverage_status,s.eligible_for_reorganization_baseline FROM scan_history h
                LEFT JOIN scan_snapshots s ON s.scan_id=h.scan_id AND s.state='FINALIZED'
                WHERE h.source_id=? AND h.scan_id>? ORDER BY h.scan_id LIMIT ?""", (source_id, after_id, limit))]

    def manifest(self, snapshot_id, *, after_id=0, limit=100):
        page_limit(limit)
        with self.database.session() as connection:
            return [dict(row) for row in connection.execute("""SELECT e.* FROM scan_manifest_entries e
                JOIN scan_snapshots s ON s.snapshot_id=e.snapshot_id AND s.state='FINALIZED'
                WHERE e.snapshot_id=? AND manifest_entry_id>? ORDER BY manifest_entry_id LIMIT ?""", (snapshot_id, after_id, limit))]

    def folders(self, snapshot_id, *, after_key=None, limit=100):
        page_limit(limit)
        boundary = "AND folder_key>?" if after_key is not None else ""
        parameters = (snapshot_id, after_key, limit) if after_key is not None else (snapshot_id, limit)
        with self.database.session() as connection:
            return [dict(row) for row in connection.execute(f"""SELECT folder_key,MIN(folder_path) AS folder_path,
                COUNT(*) AS supported_media_count,SUM(file_size) AS total_bytes
                FROM scan_manifest_entries WHERE snapshot_id=? {boundary}
                GROUP BY folder_key ORDER BY folder_key LIMIT ?""", parameters)]

    def recover_interrupted(self, probe):
        """Never infer death from an access-denied/unknown process check."""
        after, recovered = 0, 0
        while True:
            with self.database.session() as connection:
                rows = connection.execute("SELECT scan_id,owner_pid,owner_start_token FROM scan_sessions WHERE status='RUNNING' AND scan_id>? ORDER BY scan_id LIMIT 100", (after,)).fetchall()
            if not rows:
                return recovered
            for row in rows:
                after = row["scan_id"]
                # Ownerless rows are legacy M4 scans, incompatible with M5 capture.
                alive = probe.is_alive(row["owner_pid"], row["owner_start_token"]) if row["owner_pid"] is not None else False
                if alive is not False:
                    continue
                with self.database.transaction() as connection:
                    changed = connection.execute("""UPDATE scan_sessions SET status='FAILED',
                        interrupted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                        completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                        coverage_complete=0,observations_sealed=1,identity_verified=0,
                        reconciliation_performed=0,missing_marked=0
                        WHERE scan_id=? AND status='RUNNING' AND owner_pid IS ? AND owner_start_token IS ?""",
                        (row["scan_id"], row["owner_pid"], row["owner_start_token"])).rowcount
                    if changed:
                        connection.execute("""INSERT INTO scan_issues(scan_id,path,category,user_message,technical_message)
                            SELECT scan_id,root_path,'INTERRUPTED','The owning process ended before the scan completed. No missing reconciliation or snapshot was created.',
                            'Abandoned scan classified without traversing the source or resuming work.' FROM scan_sessions WHERE scan_id=?""", (row["scan_id"],))
                        recovered += 1
