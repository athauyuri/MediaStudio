"""Versioned schema history. Add migrations; never rewrite released versions."""
from dataclasses import dataclass
from hashlib import sha256
import sqlite3

from app.database.migrations.v001_foundation import STATEMENTS
from app.database.migrations.v002_source_identity import STATEMENTS as SOURCE_IDENTITY_STATEMENTS
from app.database.migrations.v003_scanner import STATEMENTS as SCANNER_STATEMENTS
from app.database.migrations.v004_snapshots import STATEMENTS as SNAPSHOT_STATEMENTS
from app.utils.windows_paths import migration_path_key, relative_path_key


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        return sha256("\n".join(self.statements).encode("utf-8")).hexdigest()


MIGRATIONS = (Migration(1, "foundation", STATEMENTS),
              Migration(2, "source_identity", SOURCE_IDENTITY_STATEMENTS),
              Migration(3, "scanner", SCANNER_STATEMENTS),
              Migration(4, "snapshots", SNAPSHOT_STATEMENTS))


def migrate(connection: sqlite3.Connection,
            migrations: tuple[Migration, ...] = MIGRATIONS) -> None:
    """Caller owns the transaction, including history creation and all DDL.

    execute(), rather than executescript(), preserves transaction boundaries.
    BEGIN IMMEDIATE in the caller serializes concurrent initializations.
    """
    versions = [migration.version for migration in migrations]
    # Pure lexical function; no root probing during schema upgrade.
    connection.create_function("source_path_key", 1, migration_path_key, deterministic=True)
    connection.create_function("source_relative_key", 1, relative_path_key, deterministic=True)
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError("Migration versions must be ordered and contiguous from 1")
    connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""")
    applied = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version").fetchall()
    if [row[0] for row in applied] != versions[:len(applied)] or len(applied) > len(versions):
        raise ValueError("Database migration history is newer or incompatible")
    for row, migration in zip(applied, migrations):
        if (row[1], row[2]) != (migration.name, migration.checksum):
            raise ValueError(f"Migration {migration.version} history mismatch")
    for migration in migrations[len(applied):]:
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, name, checksum) VALUES (?, ?, ?)",
            (migration.version, migration.name, migration.checksum))
