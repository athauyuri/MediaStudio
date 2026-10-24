from app.database.repositories.base import RecordRepository
from app.services.source_errors import SourceError
from app.utils.windows_paths import contains_path


class SourceRepository(RecordRepository):
    table = "sources"
    id_column = "source_id"
    required = frozenset({"name", "root_path"})
    writable = frozenset({"name", "root_path", "volume_identifier", "current_mount",
                          "enabled", "online", "last_scan", "watch_enabled",
                          "normalized_root_path", "volume_guid", "volume_serial",
                          "volume_relative_path", "source_identity_key", "last_seen_at",
                          "status_message"})

    def page(self, *, after_id=0, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Page limit must be between 1 and 1000")
        with self.database.session() as connection:
            return [dict(row) for row in connection.execute("""SELECT sources.*,
                (SELECT effective_status FROM scan_history WHERE source_id=sources.source_id
                 ORDER BY scan_id DESC LIMIT 1) AS scan_status FROM sources
                 WHERE source_id>? ORDER BY source_id LIMIT ?""", (after_id, limit))]

    @staticmethod
    def _duplicate(row: dict) -> SourceError:
        return SourceError("DUPLICATE_SOURCE",
            f"This folder is already registered as:\n{row['name']}\n{row['root_path']}",
            row["root_path"], existing=row)

    def register(self, values: dict, *, allow_nested: bool = False) -> int:
        self._validate(values)
        with self.database.transaction() as connection:
            duplicate = connection.execute("""SELECT * FROM sources
                WHERE source_identity_key = ? COLLATE NOCASE OR normalized_root_path = ?
                ORDER BY source_id LIMIT 1""",
                (values["source_identity_key"], values["normalized_root_path"])).fetchone()
            if duplicate:
                raise self._duplicate(dict(duplicate))
            # Stream source registrations, not media or the assets table.
            for row in connection.execute("SELECT * FROM sources"):
                left, right = values["normalized_root_path"], row["normalized_root_path"]
                nested = right and (contains_path(left, right) or contains_path(right, left))
                if values["volume_guid"] and row["volume_guid"] and values["volume_guid"].casefold() == row["volume_guid"].casefold():
                    a = (values["volume_relative_path"] or "").casefold()
                    b = (row["volume_relative_path"] or "").casefold()
                    nested = nested or a != b and (not a or not b or contains_path(a, b) or contains_path(b, a))
                if nested and not allow_nested:
                    raise SourceError("NESTED_CONFIRMATION",
                        f"This folder overlaps the registered source {row['name']}. "
                        "Future scans may index overlapping content. Register it anyway?",
                        values["root_path"])
            columns = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            return connection.execute(f"INSERT INTO sources ({columns}) VALUES ({marks})",
                                      tuple(values.values())).lastrowid

    def update_identity(self, source_id: int, values: dict) -> bool:
        self._validate(values)
        with self.database.transaction() as connection:
            duplicate = connection.execute("""SELECT * FROM sources WHERE source_id != ?
                AND (source_identity_key = ? COLLATE NOCASE OR normalized_root_path = ?)
                ORDER BY source_id LIMIT 1""", (source_id, values["source_identity_key"],
                                               values["normalized_root_path"])).fetchone()
            if duplicate:
                raise self._duplicate(dict(duplicate))
            assignments = ", ".join(f"{key} = ?" for key in values)
            return connection.execute(f"UPDATE sources SET {assignments}, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE source_id = ?",
                (*values.values(), source_id)).rowcount == 1

    def eligible_page(self, *, after_id: int = 0, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid page size")
        with self.database.session() as connection:
            return [dict(row) for row in connection.execute("""SELECT * FROM sources
                WHERE source_id > ? AND enabled = 1 ORDER BY source_id LIMIT ?""", (after_id, limit))]

    def remove_registration(self, source_id: int) -> None:
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM scan_sessions WHERE source_id=? AND status='RUNNING'", (source_id,)).fetchone():
                raise SourceError("SCAN_ACTIVE", "This source is being scanned. Cancel or finish its scan before removal.")
            if connection.execute("SELECT 1 FROM assets WHERE source_id = ? LIMIT 1",
                                  (source_id,)).fetchone():
                raise SourceError("SOURCE_REFERENCED", "This source has indexed assets and cannot be removed. Disable it instead. No folders or files were deleted.")
            connection.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
