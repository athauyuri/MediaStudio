# M4 — Scanner

Milestone: M4 — Scanner  
Status: complete, 2026-10-24. M3 was accepted before this work. All three root
planning documents were reread. M5 has not started; earlier milestone reports remain
historical records of their milestone-time scope.

## Implemented

The Sources page now has explicit **Scan Source**, **Cancel Scan**, **Show Last Scan**,
and **More Scan Issues** actions, scan-state rows, indeterminate activity, live counters,
and persisted operational result/error display. No automatic startup, page-open,
registration, status-refresh, or drive-connect scanning was added.

ScanService calls repositories and the existing M3 root/volume resolver from a worker.
UI widgets contain no SQL, filesystem enumeration, Windows API calls, or direct
repository access. Worker updates are throttled to at most ten live messages per
second; there is no Qt object or signal per asset. The final result includes persisted
counts. File totals are unknown during traversal, so no percentage is invented.

## Scope and supported types

The central `media_types.py` registry recognizes these extensions case-insensitively:

- Images: `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.gif`, `.tif`, `.tiff`.
- Videos: `.mp4`, `.mov`, `.mkv`, `.avi`, `.webm`.

Only source ID, display/relative path, filename, normalized extension, media type,
filesystem size/creation/modification timestamps, index time, and observation/missing
state are recorded. Windows birth time is used where available, otherwise filesystem
ctime. Timestamps are UTC ISO text. No media content is opened/read/decoded. There is
no image dimension, duration, codec, FPS, quick/full hash, thumbnail, poster, proxy,
duplicate detection, FFmpeg/ffprobe, ComfyUI, rename, copy, move, or deletion behavior.
Media metadata and thumbnails remain future milestones.

## Eligibility, identity, and concurrency

A scan checks that its registered source is enabled, freshly resolves root status via
M3 rules, and requires an online current root with a comparison identity. Missing,
disabled, unresolved/conflicting, protected, or offline roots fail before creating a
scan session. GUID-backed sources may resolve a changed drive letter before the scan
starts. The scan then anchors that current root and repeatedly verifies the same
identity, including before chunk writes and before reconciliation. It does not follow
a newly mounted letter/volume during a scan. An unexpected identity or unavailable
source is fatal; relevant source-level failures mark the retained source offline after
the session becomes FAILED. Fallback identities retain M3's documented limitations.

BEGIN IMMEDIATE atomically claims a source after checking for concurrent changes.
A partial unique index prevents two RUNNING sessions for the same source. The same
transaction enforces a configurable global active-scan limit (default 1, range 1–4).
This is disk concurrency, not a logical batch-size limit. The current page controls
one scan at a time. The existing worker pool remains bounded independently.

Migration 3 adds triggers preventing any source update/removal while its scan is
RUNNING. Services translate conflicts into meaningful messages; UI actions also
disable while its scan is active. This protects registration, identity, and enable
state even if another worker attempts a conflicting operation. FK safety remains.

## Traversal and bounded memory

Traversal is iterative using `os.scandir()` and non-following entry/root stat calls.
Each directory iterator closes before the next directory is entered. Pending
directories are streamed into a SQLite frontier rather than held in a Python tree,
stack of open iterators, or million-path queue. The frontier returns one directory at
a time. Files, queued directories, and issues share a bounded buffer, default 250
entries; callers can configure 1–1000. Remaining entries flush at directory boundaries.
No full asset table or set of discovered IDs is accumulated.

Directory symlinks, junctions, and all entries carrying the Windows reparse attribute
are skipped before descent. File symlinks and special entries are skipped as well;
their target contents are never opened. Queued directories are checked again before
enumeration, protecting ordinary changes between discovery and descent. Paths reuse
M3's lexical, case-insensitive Windows policy; no realpath or recursive alias resolution
is introduced. The scanner does not claim an atomic snapshot of a concurrently
changing filesystem or hardened protection against malicious reparse swaps between
an OS stat and enumeration call.

Application exclusions are centralized in immutable ScanPolicy. Startup always
protects the whole application project directory, database directory, selected config
directory, and runtime data/cache/logs/config directories. TOML `scan_excluded_paths`
adds roots; relative values are interpreted under the runtime root. Exclusion checks
are lexical, happen before stat/enumeration of an excluded child, and protect all
descendants regardless of filename extension. A source rooted inside a protected
directory is rejected. Skips are visible as retained issues.

## Persistence and asset updates

Discovery buffers commit in short transactions rather than one traversal-long
transaction or one transaction per media file. The disk frontier also uses short
transactions. Indexed source-relative and absolute comparison keys provide bounded
point lookups for each discovery. SQLite-generated asset IDs remain stable across
rediscovery, case-only path changes, and root remounts. Basic fields and missing state
are updated without erasing existing metadata, hashes, ratings, favorites, or managed
flags. The ordinary AssetRepository now prepares comparison keys too, so fixtures and
future callers follow the same policy; real older schemas remain usable for upgrades.

Ambiguous legacy duplicate paths or assets already owned by an overlapping source
are reported as PATH_CONFLICT. They are not silently merged or reassigned. Nested
sources remain allowed, but the existing single-owner absolute-path foundation means
such a later scan may report ownership conflicts. No content duplicate analysis occurs.

## Reconciliation, cancellation, and error behavior

Every observed asset records `last_seen_scan_id`. Missing detection is database-driven,
not a Python set of all discoveries. Only a fully traversed, identity-verified scan
with complete coverage may mark unseen supported assets missing. It retains records,
thumbnails, and unrelated metadata. A later observation clears missing again.

Any inaccessible entry, stat/timestamp failure, reparse skip, protected exclusion,
special entry, or path conflict makes coverage incomplete. Traversal may still finish
as COMPLETED with warnings/errors, but missing reconciliation is suppressed for the
entire source. Unsupported ordinary extensions are expected filtering and do not
by themselves invalidate coverage. The UI explicitly distinguishes completed traversal
from performed reconciliation. Last Scan updates only on completed traversal.

Reconciliation uses one final source-scoped SQL UPDATE in the same transaction as
COMPLETED status, missing count, and source last-scan time. This final SQL step may
touch many rows; it is deliberately atomic, rather than committing partial missing
flags and later labelling a cancelled scan incomplete. A SQLite progress handler can
interrupt it on cancellation, rolling back the whole final step. Ordinary discovery
writes remain chunked. No long transaction spans filesystem enumeration.

Cancellation checkpoints run between directory/entry operations and chunks. Current
bounded work completes/rolls back, uncommitted discoveries are discarded, previous
committed discoveries remain valid, and the session becomes CANCELLED without missing
reconciliation. Window close uses the same cooperative cancellation path. No persistent
pause/resume engine was added. Already-completed final commits remain completed if a
cancel request arrives after the commit boundary.

Individual child errors retain path, category, understandable and technical messages,
and timestamp, then continue only if the original root still verifies. A visible file
whose stat/timestamps cannot be read is not inserted with invented metadata. Root
enumeration/identity failure stops with FAILED; unexpected worker exceptions also
record a failure. A database outage can prevent recording final state and is surfaced
through existing structured database/worker errors and logging. No tight retry occurs.

Issues are retained in SQLite and displayed in bounded pages of 100. Scan sessions
retain RUNNING/COMPLETED/CANCELLED/FAILED states and counters for directories, files,
supported media, new/updated assets, skipped entries, errors, and observed bytes.
Skipped counts include directories as well as files. Counters describe examined work;
cancelled results can include observations discarded before commit, while committed
new/update counts reflect successful transactions.

## Migration 3 and M5 boundary

Migration 1 and 2 were not edited. Migration 3 adds operational scan sessions, issues,
pending directories, normalized asset keys, last-seen scan references, indexes, and
source-change guards. Existing source IDs, source-identity fields, settings, assets,
and metadata survive. Backfill is lexical and performs no filesystem access. Existing
duplicate asset rows are retained; they become visible conflicts when scanned.
Repeated migration/startup remains safe through the existing version/checksum runner.

These are operational sessions, not immutable manifests or reorganization baselines.
There is no M5 snapshot table, preview workspace, or file-operation plan. No operation
may use M4 counters as authorization for a future reorganization.

## Files added

- `app/database/migrations/v003_scanner.py`
- `app/database/repositories/scans.py`
- `app/services/media_types.py`, `scan_errors.py`, `scan_policy.py`, `scanner.py`
- `tests/test_scanner.py`, `test_scanner_ui.py`, `test_scanner_safety.py`
- `docs/M4_SCANNER.md`

## Files changed

- `app/config.py`, `config/default.toml`: chunk/concurrency/exclusion configuration.
- `app/database/migrations/__init__.py`: Migration 3 and lexical relative-key function.
- `app/utils/windows_paths.py`: relative-key helper; existing M3 normalization unchanged.
- `app/database/repositories/base.py`, `assets.py`: key preparation inside transactions.
- `app/database/repositories/sources.py`, `app/services/sources.py`: scan state and guards.
- `app/workers/manager.py`: structured progress notifications/cancellation inspection.
- `app/main.py`, `app/ui/main_window.py`, `app/ui/source_page.py`: service/policy wiring,
  explicit actions, progress, cancellation, and saved issues/results.
- `tests/test_config_logging.py`: scanner configuration validation.
- `README.md`: usage, policies, tests, and scope.

## Tests run

Environment: Windows, Python 3.13.15, PySide6/Qt 6.12.0, pytest 9.1.1, pytest-qt 4.5.0.

- Complete suite: `.venv/Scripts/python.exe -m pytest -q` — **129 passed in 30.36 s**.
- `python -m compileall -q app tests` — passed.
- `python -m pip check` — passed.
- `python -m app --help` — passed without source scanning.
- UI boundary search — no raw SQL, ctypes, filesystem enumeration/stat, or direct
  repository access found in UI modules.

All tests use temporary databases/source trees or an injected synthetic filesystem
under a temporary source. No real user media was scanned.

Scanner checks cover registry/case matching, recursive Unicode discovery, filesystem
fields, null/unmodified media metadata, safe updates, stable IDs, rediscovery, missing
reconciliation, cancellation, fatal disconnect, inaccessible children, failed stat,
wrong-volume rejection, global/duplicate scan guards, conflicting source management,
overlap ownership conflicts, exclusions, unexpected failure state, and real M3 upgrade.

The routine stress fixture contains 600 real temporary media files and asserts buffers
stay within 32 entries with multiple committed chunks and paged asset results. A lazy
1,051-directory synthetic chain exceeds normal Python recursion depth while observing
at most one open iterator; it requires no physical deep Windows tree or real media.

Qt checks prove scanning occurs outside the UI thread, timer events continue during a
delayed traversal call, cancel controls work, saved results display, opening Sources
does not scan, and window close cancels/finishes workers cleanly. Existing Windows
startup/shutdown and M1–M3 regression tests remain part of the full suite.

## Safety/regression validation

The subprocess safety audit permits enumeration/stat and application DB operations
while forbidding media opens/mutations, hashing calls, subprocess execution (including
FFmpeg/ffprobe), and network activity. Intentionally invalid image/video bytes still
index by extension, proving no decoder is needed. Fixture contents and tree structure
are unchanged after scanning.

M1–M3 configuration, logging, database, migration, source identity, native startup,
worker failure, and shutdown tests all pass. Historical Migration 1 and 2 SQL and their
stored history/checksums are preserved by upgrade tests. Source registration/status
refresh still do not scan. Only explicit scan actions enumerate source trees, and
all runtime writes remain in the application database/log locations.

## Manual validation and known limitations

No human visual inspection, physical disconnect/remount, or million-file benchmark was
performed. Native launch and UI behavior were exercised automatically. A heavier local
benchmark may use an application-owned temporary synthetic tree, never real media as
a destructive test target; this milestone does not claim measured million-file throughput.

Windows filesystem/volume calls can be delayed by the OS; cooperative cancellation
takes effect between calls. Shutdown waits without blocking the event loop rather than
forcibly terminating native I/O. Abrupt process termination may leave a RUNNING session;
it is not automatically resumed or guessed complete. Such a session deliberately
blocks conflicting scans/source changes until reviewed through a future recovery or
maintenance workflow. M4 adds cancellation, not crash-resume/repair UI.

Missing reconciliation is intentionally conservative for any incomplete coverage,
including application exclusions. Alias/reparse-race limitations are described above.
M5 immutable snapshots, metadata extraction, and thumbnails remain unimplemented.

Recommended next milestone: **M5 — Scan Snapshot / Manifest**, only on explicit instruction.
