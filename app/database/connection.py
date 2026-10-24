"""Connection factory. No connection is retained or shared across threads."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from app.database.errors import DatabaseError
from app.database.migrations import MIGRATIONS, Migration, migrate


class Database:
    def __init__(self, path: Path, busy_timeout_ms: int = 5000):
        self.path = Path(path).absolute()
        self.busy_timeout_ms = busy_timeout_ms
        self._initialized = False
        self._closed = False

    def initialize(self, migrations: tuple[Migration, ...] = MIGRATIONS) -> None:
        if self._closed:
            raise RuntimeError("Database is closed")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._session(create=True) as connection:
                with self._transaction(connection):
                    migrate(connection, migrations)
            self._initialized = True
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise DatabaseError("initialize", self.path, exc) from exc

    @contextmanager
    def _session(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            # URI mode=rw prevents accidental recreation if an initialized DB vanishes.
            uri = self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw")
            connection = sqlite3.connect(uri, uri=True,
                                        timeout=self.busy_timeout_ms / 1000,
                                        isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            connection.execute("PRAGMA foreign_keys=ON")
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode.lower() != "wal":
                raise ValueError("SQLite WAL mode could not be enabled")
            yield connection
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    @contextmanager
    def _transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        if self._closed or not self._initialized:
            raise RuntimeError("Database must be initialized and open")
        try:
            with self._session() as connection:
                yield connection
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise DatabaseError("access", self.path, exc) from exc

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.session() as connection:
            with self._transaction(connection):
                yield connection

    def close(self) -> None:
        """No idle connections to wait for; callers must finish sessions first."""
        self._closed = True
