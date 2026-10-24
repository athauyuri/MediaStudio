from app.database.connection import Database


class RecordRepository:
    """Internal CRUD foundation with fixed identifiers and bound values.

    Each method owns a short-lived connection/transaction. Page reads use a stable
    integer cursor and a bounded result size rather than loading the whole table.
    """
    table: str
    id_column: str
    writable: frozenset[str]
    required: frozenset[str]

    def __init__(self, database: Database):
        self.database = database

    def _validate(self, values: dict) -> None:
        if not values or set(values) - self.writable:
            raise ValueError("Empty values or unsupported repository fields")

    def _prepare(self, values: dict, connection) -> dict:
        return values

    def create(self, **values) -> int:
        self._validate(values)
        if self.required - set(values):
            raise ValueError("Required repository fields are missing")
        with self.database.transaction() as connection:
            values = self._prepare(values, connection)
            columns = ", ".join(values)
            placeholders = ", ".join("?" for _ in values)
            cursor = connection.execute(
                f"INSERT INTO {self.table} ({columns}) VALUES ({placeholders})",
                tuple(values.values()))
            return cursor.lastrowid

    def get(self, record_id: int) -> dict | None:
        with self.database.session() as connection:
            row = connection.execute(
                f"SELECT * FROM {self.table} WHERE {self.id_column} = ?", (record_id,)).fetchone()
            return dict(row) if row else None

    def page(self, *, after_id: int = 0, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Page limit must be between 1 and 1000")
        with self.database.session() as connection:
            rows = connection.execute(
                f"SELECT * FROM {self.table} WHERE {self.id_column} > ? "
                f"ORDER BY {self.id_column} LIMIT ?", (after_id, limit)).fetchall()
            return [dict(row) for row in rows]

    def update(self, record_id: int, **values) -> bool:
        self._validate(values)
        with self.database.transaction() as connection:
            values = self._prepare(values, connection)
            assignments = ", ".join(f"{key} = ?" for key in values)
            if self.table == "sources":
                assignments += ", updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
            return connection.execute(
                f"UPDATE {self.table} SET {assignments} WHERE {self.id_column} = ?",
                (*values.values(), record_id)).rowcount == 1

    def delete(self, record_id: int) -> bool:
        """Delete an explicit DB record only; never delete its filesystem path."""
        with self.database.transaction() as connection:
            return connection.execute(
                f"DELETE FROM {self.table} WHERE {self.id_column} = ?", (record_id,)).rowcount == 1
