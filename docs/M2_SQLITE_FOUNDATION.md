# M2 — SQLite Foundation

Status: complete, 2026-10-24. M1 was accepted before this work. All three root
planning documents were reread. Only M2 was implemented; M3 has not started.
The M1 report remains a historical record of its validation and scope at that time.

## Architecture and lifecycle

`AppConfig.database_path` defaults to `data/media_studio.db`. Relative paths resolve
against the explicit application runtime root; absolute paths are used as configured.
TOML remains the source for startup/runtime configuration. The database location is
an application-owned path, not a registered media source or an unrelated database.

Startup loads config, initializes logging, explicitly initializes `Database`, and
only then creates QApplication and the main window. Database setup happens before
the Qt event loop exists, so no migration work runs inside UI callbacks. Failures
return exit code 1, log technical context, and print a user-facing explanation that
includes the configured path. The normal window is never created on DB failure.

Constructing/importing database classes does not create files. Initialization creates
only the configured parent directory and SQLite-owned files. Each session gets its
own connection on the calling thread, with SQLite's default thread check retained.
No connection is cached globally or passed between threads. Connections have WAL,
foreign keys, UTF-8 text, and a 5,000 ms busy timeout. Sessions close in `finally`,
including failure paths. `transaction()` uses BEGIN IMMEDIATE, commits on success,
and rolls back on exceptions. Database close rejects new sessions; it has no idle
connection pool or shutdown wait. Existing sessions must leave their context before
their owning worker exits; the existing Qt shutdown lifecycle waits for workers.

Normal session opens use SQLite URI `mode=rw`, so a missing initialized database is
reported rather than silently recreated. Structured `DatabaseError` contains operation,
path, SQLite error code, technical message, exception type, retryable flag, and a
user message. There is no automatic retry loop.

## Schema and IDs

Migration 1 creates only `settings`, `sources`, `assets`, and `schema_migrations`
(plus SQLite's internal sequence table). No scanner or source manager is present.

Sources/assets use `INTEGER PRIMARY KEY AUTOINCREMENT`. SQLite stores these compactly
as row IDs, keeping future large-library indexes smaller than text UUID keys.
AUTOINCREMENT prevents reuse of deleted IDs. IDs are stable when paths change and
are local to this database; cross-database merging would require explicit ID mapping.
Paths are attributes, never primary keys. Source deletion is restricted while assets
reference it, preserving indexed records rather than cascading away offline sources.

Unknown metadata is nullable. Boolean/rating/size/dimension constraints reject invalid
values. New sources default offline until a later milestone can determine status.
Absolute asset paths and source root paths have unique constraints. Path strings are
stored verbatim; volume identity discovery, Windows path normalization/case-alias
policy, and source registration validation belong to later source/scanner work.

Assets have indexes for source ID, filename, media type, modified time, missing,
managed, and a partial content-hash index excluding unknown hashes. The unique
absolute-path constraint supplies its index without an additional redundant index.

## Migration policy

Project-owned Python migration descriptors contain ordered SQL statements. The runner
validates contiguous versions starting at 1, compares applied names/checksums, and
rejects unknown newer or incompatible histories. BEGIN IMMEDIATE serializes competing
initializers; the history is read only after that transaction starts.

All pending SQL and history inserts run in one transaction, including history-table
creation. Individual `execute()` calls preserve SQLite DDL rollback behavior; the
runner does not use `executescript()`. Any failure rolls back the pending schema/history
changes and stops initialization. A newly created empty database file or SQLite WAL
configuration may remain after failure; no migration is reported applied. Existing
successful migrations and stored data are retained.

Never edit the behavior of migration 1 after release/use. Future schema changes must
add a new version to the migration registry. Checksums cover migration SQL, not media;
M2 performs no media hashing. Databases are never deleted/rebuilt to upgrade.

## Repository boundary

`SettingsRepository` provides text-key get/set (upsert)/delete.
`SourceRepository` and `AssetRepository` provide create/get/update/delete and bounded
keyset page reads. Each method owns a short session or transaction. Updates use fixed
field allowlists and bound values; callers cannot supply SQL identifiers. Page sizes
range from 1 to 1,000; no method loads the full library. Deletes affect an explicit
database record only, never the referenced file or directory. Multi-operation atomic
work can use `Database.transaction()` in a future service without nesting repository
transactions.

Example for an application-owned DB (not a media path):

```python
from pathlib import Path
from app.database.connection import Database
from app.database.repositories import SettingsRepository

database = Database(Path("data/media_studio.db"))
database.initialize()
try:
    settings = SettingsRepository(database)
    settings.set("example.preference", "value")
    value = settings.get("example.preference")
finally:
    database.close()
```

UI widgets contain no raw SQL and do not call the database. Services/workers will
consume these boundaries later. No repository automatically discovers or inspects
anything at the stored filesystem paths.

## Files added

- `app/database/__init__.py`, `connection.py`, `errors.py`
- `app/database/migrations/__init__.py`, `v001_foundation.py`
- `app/database/repositories/__init__.py`, `base.py`, `settings.py`, `sources.py`, `assets.py`
- `tests/test_database.py`, `tests/test_database_startup.py`
- `docs/M2_SQLITE_FOUNDATION.md`

## Files changed

- `app/config.py`, `config/default.toml`: database path configuration and validation.
- `app/main.py`: explicit initialization, startup failure handling, shutdown cleanup.
- `app/ui/main_window.py`: status text reflects M2; navigation remains placeholders.
- `tests/test_config_logging.py`: path validation and Unicode/Windows configuration.
- `tests/test_startup.py`: expected application-owned database creation.
- `README.md`: database setup, migrations, tests, scope, and lifecycle instructions.

## Tests run

Windows, Python 3.13.15, PySide6/Qt 6.12.0, pytest 9.1.1, pytest-qt 4.5.0.

- Complete suite: `.venv/Scripts/python.exe -m pytest -q` — **43 passed**.
- Compile check: `python -m compileall -q app tests` — passed.
- Dependency integrity: `python -m pip check` — passed.
- CLI help: `python -m app --help` — passed without DB initialization.

New coverage includes fresh schema, WAL/FK/UTF-8/timeout pragmas, schema history and
indexes, idempotent initialization, incompatible histories, commit/rollback, closed
connections, failed fresh/upgrade migration rollback, repository CRUD, parameter binding,
nullable metadata, stable IDs, bounded pagination, FK restrictions, thread-owned sessions,
concurrent initialization, closed DB rejection, missing-file protection, and import safety.

Subprocess tests display the real native Qt window and close it normally. Invalid DB
bytes, a directory target, an unusable parent, and an injected failed migration all
abort startup before main-window construction. Invalid existing DB bytes are preserved.
Repeated launches preserve a setting and indexed source/asset records. A subprocess
audit hook and guarded stat/lstat reject any attempt to open, enumerate, inspect, or
modify the registered temporary media folder; both launches pass. Original fixture
bytes and folder contents remain unchanged. Tests use temporary databases/media only.

## Manual validation

No human visual inspection was performed. Native Windows launch, navigation, event
loop responsiveness, failure isolation, worker cancellation, and shutdown are covered
by automated Qt/subprocess tests. The existing Windows startup launcher is retained.

## Safety/regression validation and limitations

All M1 checks pass, with its startup expectation updated to include the authorized
application DB. No source discovery, scanning, metadata extraction, thumbnail creation,
FFmpeg, media hashing, ComfyUI connection, reorganization, or media file operation was
introduced. Runtime writes are limited to application logs and the configured SQLite
database/sidecars. Repository CRUD is infrastructure, with no new feature UI.

Future workers must keep DB transactions short and perform large DB work off the UI
thread. Migration failure recovery requires correcting the cause and retrying startup;
there is no automatic destructive repair. Packaging and database backup/export UI
remain outside M2.

Recommended next milestone: **M3 — Source Manager**, only on explicit instruction.
