"""Bounded scan persistence. No filesystem access and no media decoding."""
from app.database.connection import Database
from app.services.media_types import SUPPORTED_EXTENSIONS
from app.services.scan_errors import ScanError
from app.services.process_ownership import ProcessProbe
import ntpath

COUNTERS = ("files_seen", "supported_files", "directories_seen", "files_indexed",
            "files_updated", "files_skipped", "errors", "bytes_seen", "coverage_complete", "unsupported_files")
BASIC_FIELDS = ("absolute_path", "relative_path", "filename", "extension", "media_type",
                "file_size", "created_time", "modified_time", "indexed_time",
                "normalized_absolute_path", "normalized_relative_path")


class ScanRepository:
    def __init__(self, database: Database):
        self.database = database

    def claim(self, row: dict, concurrency: int) -> int:
        owner_pid, owner_token = ProcessProbe().current()
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM scan_sessions WHERE source_id=? AND status='RUNNING'",
                                  (row["source_id"],)).fetchone():
                raise ScanError("SCAN_ACTIVE", "This source is already being scanned.")
            if connection.execute("SELECT COUNT(*) FROM scan_sessions WHERE status='RUNNING'").fetchone()[0] >= concurrency:
                raise ScanError("SCAN_CAPACITY", "Another scan is active. Wait for it to finish or cancel it.")
            current = connection.execute("SELECT * FROM sources WHERE source_id=?", (row["source_id"],)).fetchone()
            if not current or not current["enabled"] or not current["online"] or current["root_path"] != row["root_path"] or current["source_identity_key"] != row["source_identity_key"]:
                raise ScanError("SOURCE_CHANGED", "The source changed before the scan could start. Refresh it and retry.")
            scan_id = connection.execute("""INSERT INTO scan_sessions(source_id,root_path,status,
                owner_pid,owner_start_token,source_identity_at_scan,source_name_at_scan,mount_at_scan,observations_format)
                VALUES (?,?,'RUNNING',?,?,?,?,?,1)""", (row["source_id"], row["root_path"], owner_pid,
                owner_token, row["source_identity_key"], row["name"], row["current_mount"])).lastrowid
            connection.execute("INSERT INTO scan_pending_directories VALUES (?,?,?)",
                               (scan_id, row["normalized_root_path"], row["root_path"]))
            return scan_id

    def active(self, source_id: int) -> bool:
        with self.database.session() as connection:
            return connection.execute("SELECT 1 FROM scan_sessions WHERE source_id=? AND status='RUNNING'", (source_id,)).fetchone() is not None

    def next_directory(self, scan_id: int) -> str | None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT path_key,path FROM scan_pending_directories WHERE scan_id=? ORDER BY path_key LIMIT 1", (scan_id,)).fetchone()
            if row is None:
                return None
            connection.execute("DELETE FROM scan_pending_directories WHERE scan_id=? AND path_key=?", (scan_id, row[0]))
            return row[1]

    @staticmethod
    def _issue(connection, scan_id, issue):
        connection.execute("INSERT INTO scan_issues(scan_id,path,category,user_message,technical_message) VALUES (?,?,?,?,?)",
            (scan_id, issue["path"], issue["category"], issue["user_message"], issue["technical_message"]))

    @staticmethod
    def _observe(connection, scan_id, values, asset_id, status="OBSERVED"):
        connection.execute("""INSERT INTO scan_observations(scan_id,asset_id,absolute_path_at_scan,
            relative_path_at_scan,filename,extension,media_type,file_size,created_time,modified_time,
            comparison_key,folder_key,folder_path,observation_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (scan_id, asset_id, values["absolute_path"], values["relative_path"], values["filename"],
             values["extension"], values["media_type"], values["file_size"], values["created_time"],
             values["modified_time"], values["normalized_relative_path"],
             ntpath.dirname(values["normalized_relative_path"]), ntpath.dirname(values["relative_path"]), status))

    def write_chunk(self, scan_id: int, source_id: int, assets: list[dict], directories: list[tuple],
                    issues: list[dict], counters: dict, current_folder: str) -> None:
        new, updated, conflicts = 0, 0, 0
        with self.database.transaction() as connection:
            for values in assets:
                matches = connection.execute("SELECT * FROM assets WHERE source_id=? AND normalized_relative_path=? LIMIT 2",
                    (source_id, values["normalized_relative_path"])).fetchall()
                absolute = connection.execute("SELECT asset_id,source_id FROM assets WHERE normalized_absolute_path=? LIMIT 2",
                    (values["normalized_absolute_path"],)).fetchall()
                if len(matches) > 1 or any(row["source_id"] != source_id for row in absolute) or (matches and any(row["asset_id"] != matches[0]["asset_id"] for row in absolute)):
                    conflicts += 1
                    self._issue(connection, scan_id, dict(path=values["absolute_path"], category="PATH_CONFLICT",
                        user_message="This path conflicts with an existing asset registration; it was not reassigned or merged.", technical_message="Ambiguous source-relative or overlapping-source absolute path."))
                    self._observe(connection, scan_id, values, None, "PATH_CONFLICT")
                    continue
                if matches:
                    row = matches[0]
                    changed = row["missing"] or any(row[key] != values[key] for key in BASIC_FIELDS if key != "indexed_time")
                    assignments = ",".join(key + "=?" for key in BASIC_FIELDS)
                    connection.execute(f"UPDATE assets SET {assignments},missing=0,last_seen_scan_id=? WHERE asset_id=?",
                        (*[values[key] for key in BASIC_FIELDS], scan_id, row["asset_id"]))
                    updated += bool(changed)
                    asset_id = row["asset_id"]
                else:
                    keys = ",".join(BASIC_FIELDS)
                    marks = ",".join("?" for _ in BASIC_FIELDS)
                    asset_id = connection.execute(f"INSERT INTO assets(source_id,{keys},missing,last_seen_scan_id) VALUES (?,{marks},0,?)",
                        (source_id, *[values[key] for key in BASIC_FIELDS], scan_id)).lastrowid
                    new += 1
                self._observe(connection, scan_id, values, asset_id)
            connection.executemany("INSERT OR IGNORE INTO scan_pending_directories VALUES (?,?,?)",
                                   ((scan_id, key, path) for key, path in directories))
            for issue in issues:
                self._issue(connection, scan_id, issue)
            saved = dict(counters)
            saved["files_indexed"] += new
            saved["files_updated"] += updated
            saved["errors"] += conflicts
            saved["files_skipped"] += conflicts
            if conflicts:
                saved["coverage_complete"] = 0
            assignments = ",".join(key + "=?" for key in COUNTERS)
            connection.execute(f"UPDATE scan_sessions SET {assignments}, current_folder=? WHERE scan_id=?",
                               (*[saved[key] for key in COUNTERS], current_folder, scan_id))
        counters.update(saved)

    def finish(self, scan_id: int, status: str, counters: dict, *, stopped=lambda: False, identity_verified=False) -> dict:
        with self.database.transaction() as connection:
            if status == "COMPLETED":
                # Atomic reconciliation/status commit. Interrupt rolls the whole final step back.
                connection.set_progress_handler(lambda: int(stopped()), 1000)
            try:
                if status == "COMPLETED" and stopped():
                    raise ScanError("CANCEL_REQUESTED", "Scan cancellation requested.")
                missing = 0
                reconcile = status == "COMPLETED" and bool(counters["coverage_complete"])
                if reconcile:
                    marks = ",".join("?" for _ in SUPPORTED_EXTENSIONS)
                    missing = connection.execute(f"""UPDATE assets SET missing=1 WHERE source_id=
                        (SELECT source_id FROM scan_sessions WHERE scan_id=?)
                        AND (last_seen_scan_id IS NULL OR last_seen_scan_id != ?) AND missing=0
                        AND lower(extension) IN ({marks})""", (scan_id, scan_id, *sorted(SUPPORTED_EXTENSIONS))).rowcount
                if status == "COMPLETED" and stopped():
                    raise ScanError("CANCEL_REQUESTED", "Scan cancellation requested.")
                connection.execute("""UPDATE scan_sessions SET status=?, completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    reconciliation_performed=?,missing_marked=?,observations_sealed=1,identity_verified=? WHERE scan_id=?""",
                    (status, reconcile, missing, bool(identity_verified and status == "COMPLETED"), scan_id))
                # Triggers allow source updates only after its scan leaves RUNNING.
                if status == "COMPLETED":
                    connection.execute("""UPDATE sources SET last_scan=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                        WHERE source_id=(SELECT source_id FROM scan_sessions WHERE scan_id=?)""", (scan_id,))
                connection.execute("DELETE FROM scan_pending_directories WHERE scan_id=?", (scan_id,))
            finally:
                connection.set_progress_handler(None, 0)
        return self.get(scan_id)

    def get(self, scan_id: int) -> dict:
        with self.database.session() as connection:
            row = dict(connection.execute("SELECT * FROM scan_history WHERE scan_id=?", (scan_id,)).fetchone())
            row["status"] = row["effective_status"]
            return row

    def latest(self, source_id: int) -> dict | None:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM scan_history WHERE source_id=? ORDER BY scan_id DESC LIMIT 1", (source_id,)).fetchone()
            if row:
                result = dict(row)
                result["status"] = result["effective_status"]
                return result
            return None

    def issues(self, scan_id: int, *, after_id: int = 0, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid issue page size")
        with self.database.session() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM scan_issues WHERE scan_id=? AND issue_id>? ORDER BY issue_id LIMIT ?", (scan_id, after_id, limit))]
