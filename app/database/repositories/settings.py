from app.database.connection import Database


class SettingsRepository:
    def __init__(self, database: Database):
        self.database = database

    def set(self, key: str, value: str) -> None:
        if not isinstance(key, str) or not key or not isinstance(value, str):
            raise ValueError("Settings require a non-empty text key and text value")
        with self.database.transaction() as connection:
            connection.execute("""INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""", (key, value))

    def get(self, key: str) -> dict | None:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM settings WHERE key = ?", (key,)).fetchone()
            return dict(row) if row else None

    def delete(self, key: str) -> bool:
        with self.database.transaction() as connection:
            return connection.execute("DELETE FROM settings WHERE key = ?", (key,)).rowcount == 1
