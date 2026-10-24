# Local Media Studio

Native Windows application using Python 3.13 and PySide6. **M1–M5** are implemented:
navigation placeholders, read-only TOML configuration, rotating logs, cooperative
background workers, a local SQLite foundation, a Source Manager, and an explicit
non-destructive filesystem scanner, and immutable scan snapshots/manifests. Media
contents are never opened by scanning or snapshot review.

From PowerShell in this directory:

```powershell
python --version  # must be Python 3.13
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m app
```

No environment activation is required. `python -m app --help` lists options.
After setup, double-click `startMedia Studio.cmd` in the project folder to launch the app.
It also works from PowerShell and forwards options, for example `& '.\startMedia Studio.cmd' --help`.
The launcher uses the project's virtual environment and runs from the project
directory, regardless of where it was invoked. Startup errors remain visible.
For PowerShell, `scripts/run.ps1` is also available.

The default configuration is `config/default.toml`; loading it never rewrites it.
Missing configuration uses built-in defaults; invalid configuration aborts startup
with a message and a nonzero exit code. To choose another configuration/runtime root:

```powershell
.\.venv\Scripts\python.exe -m app --config config/default.toml --runtime-root .
```

The app writes `<runtime-root>/logs/media-studio.log` and its bounded rotating
backups, plus its configured application database and SQLite WAL/SHM sidecars.
Choose an application-owned runtime directory, never a media source. No source
folders are automatically registered, traversed, or changed.

Open **Sources** to add a folder with the native Windows picker, refresh root status,
enable/disable it, rename its display name, or remove an unreferenced registration.
**Registering a source does not scan it.** Refresh Status checks only registered roots
on the displayed page and required volume metadata. Source operations run in workers.
Offline sources and their asset records remain stored. Removal deletes the registration
only and is blocked when indexed assets or retained snapshot history reference it.
Disable such sources to retain their history.

Sources use clean display paths plus separate case-insensitive comparison keys.
Where available, a Windows volume GUID plus its relative root path provides logical
identity across drive-letter changes. Without a GUID, the UI explicitly shows
**Identity unresolved** and uses a path-only fallback, which cannot track a remount
at another letter. Nested sources are allowed after an overlap confirmation.
Selected junction/reparse roots are rejected; choose their target folder explicitly.

To scan, select a source and click **Scan Source**. A fresh root/volume check runs
before traversal. Disabled/offline sources cannot scan. **Cancel Scan** stops
cooperatively; committed discoveries remain valid. Opening the app, opening Sources,
registering a folder, or refreshing status never starts a scan automatically.

Scanning indexes extension, path, size, and filesystem timestamps only. Images:
`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.gif`, `.tif`, `.tiff`. Videos:
`.mp4`, `.mov`, `.mkv`, `.avi`, `.webm`. Matching is case-insensitive. No media
decoding, dimensions, duration/codecs, hashing, thumbnails, FFmpeg, or ComfyUI is used.
Those features belong to future milestones.

The scanner streams directory entries, skips all reparse/symbolic-link entries, and
stores pending directories in SQLite. It commits bounded discovery chunks (default
250 entries) and defaults to one active scan globally, with at most one per source.
Source registration changes are blocked while its scan is active. Counts appear
live without an assumed total/percentage; **Show Last Scan** reopens operational
results, and **More Scan Issues** pages through retained warnings/errors.

Unseen assets are marked missing only after clean, completed traversal with a final
identity check. Cancellation/failure never reconciles missing files. Inaccessible
entries, reparse skips, protected exclusions, and path conflicts conservatively
suppress missing reconciliation for the entire scan; the result says so explicitly.
Records and their existing metadata are never deleted. Rediscovery clears missing.

TOML supports `scan_chunk_size` (1–1000), `scan_concurrency` (1–4, default 1), and
`scan_excluded_paths` (additional paths, relative to the runtime root or absolute).
The application project directory, configured DB directory, config directory, and
runtime `data`, `cache`, `logs`, `config` directories are always protected. Broad
sources skip these paths before enumeration; a source inside a protected directory
cannot scan. Interrupted scans do not automatically resume traversal.

Click **Review Scan History** for a selected source. Select a scan, then **Open
Selection** to review snapshot summary, paged manifest, direct-folder totals, and
warnings/errors. Historical paths and file sizes remain fixed after current assets
change or the source remounts. Complete coverage is distinguished from incomplete
coverage, and eligibility for a future baseline is explicit. No reorganization is
authorized or performed by this view.

New completed scans create snapshots from observations persisted during scanning,
without a second filesystem pass. Failed/cancelled/interrupted scans have no valid
snapshot. Incomplete-coverage traversals may have finalized snapshots marked
**INCOMPLETE** and **NOT eligible**. Unsupported ordinary files are counted separately
and do not make coverage incomplete. Total bytes means supported media in the manifest.

**A scan snapshot is a historical database record of what a completed scan observed.
It does not modify or reorganize media.**

Snapshot construction is transactional. If it fails, the completed operational scan
and sealed observations remain available; **Retry Snapshot Build** retries from the
database only. Legacy M4 scans did not retain these observations, so they require a
new explicit scan rather than an invented historical manifest. Old snapshots and
observations are retained without automatic pruning; database storage grows with scans.

At startup and history review, abandoned RUNNING scans become **INTERRUPTED** without
missing reconciliation, snapshot creation, or traversal resume. New scans record
Windows process ID plus creation time, so live owners and PID reuse can be distinguished.
Uncertain/access-denied owner checks retain locks. Legacy ownerless M4 runs are
classified as interrupted after upgrade; close older app versions before upgrading.

The default database is `<runtime-root>/data/media_studio.db`. Set `database_path`
in TOML to change it; relative paths resolve against `--runtime-root`, while absolute
paths are used as configured. The configured database location must be application-owned.
Do not point it at an unrelated database. Startup configuration stays in TOML.

Database initialization runs explicitly after configuration/logging and before
constructing the main window. It creates the parent directory, opens SQLite locally,
enables WAL and foreign keys, applies a 5-second busy timeout, and runs versioned
migrations transactionally. Failed initialization aborts startup with a console
message and technical log; it does not open the normal window. Imports never create
a database. Connections are short-lived, thread-owned, and closed on leaving their
session; there is no shared global connection or server.

Migrations are recorded with versions, timestamps, and SQL checksums. Repeated
launches retain data and skip applied migrations. Incompatible history fails rather
than rebuilding the database. Released migrations must never be edited; add a new
version for schema changes. The initial schema contains only settings, sources,
assets, and migration tracking. Migration 2 adds source-identity metadata, preserving
existing records without accessing registered folders. Migration 3 adds operational
scan sessions/issues, a disk-backed traversal frontier, and asset observation markers.
It also upgrades existing data without filesystem access. Migration 4 adds persisted
observations, immutable snapshots/manifests, and ownership/recovery metadata without
filesystem access. Repository operations change database records only.

Run automated checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run database checks separately with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_database.py tests/test_database_startup.py
```

Run the M3 service/UI/volume/safety checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_sources.py tests/test_source_ui.py tests/test_windows_volumes.py tests/test_source_filesystem_safety.py
```

Run scanner checks (including bounded stress and filesystem audits) with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_scanner.py tests/test_scanner_ui.py tests/test_scanner_safety.py
```

Run snapshot, recovery, database-scale, UI, and finalization-audit checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_snapshots.py tests/test_snapshot_recovery.py tests/test_snapshot_large.py tests/test_snapshot_ui.py tests/test_snapshot_safety.py
```

Tests use temporary directories and real Qt widgets. They do not operate on real
media. For headless environments set `QT_QPA_PLATFORM=offscreen` explicitly.

Close the window to exit. Shutdown requests cooperative cancellation and keeps
the event loop running until workers finish. Worker functions must use cancellation
checkpoints and bounded waits; arbitrary blocking functions cannot be force-killed.

See [M1 implementation and validation](docs/M1_APPLICATION_SKELETON.md).
See [M2 architecture and validation](docs/M2_SQLITE_FOUNDATION.md) for ID strategy,
repository usage, migration policy, and scope boundaries.
See [M3 source identity, safety, and validation](docs/M3_SOURCE_MANAGER.md).
See [M4 scanner design, safety, and validation](docs/M4_SCANNER.md).
See [M5 snapshot/manifest design, recovery, and validation](docs/M5_SCAN_SNAPSHOT_MANIFEST.md).
M6 Image Metadata requires a separate instruction; it does not start automatically.
