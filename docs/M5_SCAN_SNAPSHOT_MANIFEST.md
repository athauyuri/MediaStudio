# M5 — Scan Snapshot / Manifest

Milestone: M5 — Scan Snapshot / Manifest  
Status: complete, 2026-10-24. M4 was accepted before implementation. All three root
planning documents were reread. M6 has not started. Earlier reports remain historical
records; M5 supersedes M4's lack of observation capture and interrupted-scan recovery.

**A scan snapshot is a historical database record of what a completed scan observed.
It does not modify or reorganize media.**

## Implemented

New scans persist immutable-capable supported-media observations in the same bounded
transactions as asset updates. Completed, identity-verified traversals then finalize
a separate snapshot and manifest using database-only INSERT ... SELECT. Snapshot
summaries, coverage/eligibility, direct-folder aggregates, manifests, retained issues,
and operational scan history are reviewable from **Sources → Review Scan History**.
Minimal process-aware interrupted-scan classification releases abandoned scan locks
without resuming traversal or inventing completion.

## Operational scan, observations, and snapshot

M4 `scan_sessions` remain operational execution records. They are not the file list or
future reorganization baseline. `scan_observations` now retain each successfully read
supported file's scan-time path, relative path, filename, extension, type, size,
filesystem creation/modification times, comparison key, direct containing folder,
optional historical asset ID, and observation status. Values are copied at observation
commit time, not reconstructed from the mutable assets table later.

Observations include supported paths that conflict with existing asset ownership;
these have PATH_CONFLICT status and may have no asset ID. Such observations describe
the filesystem facts that were available, while their snapshot remains ineligible.
Unreadable/stat-failed media has no invented size/timestamps and is represented in
issues and coverage status rather than a deceptive manifest row. Ordinary unsupported
files have a separate counter; their contents are never opened.

Source name, ID, comparison identity, current mount, and anchored root are captured
when the scan is claimed. A later source rename/remount cannot rewrite those facts.
All normal terminal states seal observation writes. Cancelled/failed/interrupted
observations remain non-authoritative and cannot be finalized as a valid snapshot.

`scan_snapshots` and `scan_manifest_entries` are separate historical tables. Manifest
rows contain their own values rather than joining live asset metadata. Asset IDs are
historical references without a cascading FK: explicit current-asset deletion cannot
erase or mutate history. Snapshot-to-scan references retain the scan and issue context.

## Creation and coverage policy

Finalization requires all of:

- Operational status COMPLETED, without an interruption marker.
- M5 observation capture enabled and sealed.
- Successful final source-identity verification and a recorded source identity.

Cancelled, failed, interrupted, and legacy M4 scans cannot produce an authoritative
manifest. M4 legacy scans lack exact retained observations, so the UI requires a new
explicit scan. Current assets are never substituted for missing historical evidence.

Coverage is independently recorded:

- **COMPLETE:** full intended coverage, no coverage skips/errors or path conflicts.
- **COMPLETE_WITH_WARNINGS:** coverage remains complete but warnings require review.
- **INCOMPLETE:** any unreadable/protected/reparse/special entry, path conflict, or
  other condition prevented observing the whole intended source.

Current M4 filesystem warnings generally make coverage INCOMPLETE; the complete-with-
warnings state supports the explicit policy without calling incomplete scans complete.
Unsupported ordinary extensions are normal filtering and do not invalidate coverage.

`eligible_for_reorganization_baseline` is true only for a finalized COMPLETE snapshot
from the verified, completed observation process. Path fallback identities retain M3's
limitations; this property does not waive later live identity/conflict/disk-space checks
or authorize any file operation. M5 implements no reorganization actions or planning.

## Summary definitions

Summary numbers are deliberately distinguished:

| Field | Meaning |
|---|---|
| folder_count | Directories visited/observed during traversal, including the root; an enumeration failure may count a visited directory and also produce an issue. |
| file_count | Ordinary filesystem files examined, including unsupported extensions. |
| unsupported_count | Examined ordinary files outside the central extension registry. |
| supported_candidates | Supported file candidates examined, including candidates whose timestamp/stat processing could not complete. |
| supported_media_count | Number of supported observations copied into the manifest. |
| image_count / video_count | Manifest observations of each extension-classified media type. |
| total_bytes | Sum of supported-media sizes included in the manifest, including explicitly labelled path-conflict observations. |
| skipped_count | Operational skipped entries excluding ordinary unsupported files; includes directory skips/conflicts. |
| error_count | Retained traversal error count, separate from warning-only skips. |

Manifest/image/video/byte counts are aggregated directly from persisted observations.
Directory, examined-file, unsupported, skip, and error totals preserve the completed
traversal's recorded counters. Thus operational progress alone is never treated as
the manifest. Snapshots retain the observation window's start/end times; sequential
stat observations do not claim an atomic point-in-time filesystem snapshot.

## Transactional finalization and retry

Finalization inserts a BUILDING summary, copies rows with INSERT ... SELECT, and changes
the summary to FINALIZED in one SQLite transaction. Other readers cannot see a partially
valid build. A failed copy or summary write rolls back the entire snapshot/manifest;
the completed operational scan and sealed observations remain intact. Technical errors
are logged and a separate operational `snapshot_error` is retained for review.

**Retry Snapshot Build** uses the same sealed observations and no filesystem access.
A newer scan may already have changed current assets; an older delayed build still
uses its own original observations. Repeating a successful finalization returns the
existing snapshot, avoiding duplicate history. A later actual scan receives a new
snapshot ID. Local integer IDs are displayed as SNAP-000042 / SCAN-000042.

Database triggers reject finalized snapshot updates/deletes, manifest edits/additions,
sealed-observation writes, and changes to retained scan/issue history. Source registration
removal is refused when it would discard retained snapshot context, even for an empty
snapshot. Display-name edits, enable/disable, and valid remount refresh remain possible
when no scan is RUNNING. No automatic retention cleanup or pruning exists.

## Bounded review and performance

The review dialog provides four Qt model/view tables with keyset pagination:

- Scan history (including cancelled/failed/interrupted runs and legacy completed scans).
- Manifest (relative path, filename, type, bytes, modification time, observation status).
- Folders (direct containing folder, supported-media count, supported-media bytes).
- Retained warnings/errors with path, category, user/technical messages, and timestamp.

Each page holds at most 100 rows, and public repository limits are bounded to 1–1000.
All queries, folder aggregation, history recovery, and retry work run in existing workers.
No row-per-file widgets, thumbnail readers, full-manifest Python list, or filesystem
access are introduced. Directory groups are calculated in SQL using the snapshot/folder
index and a keyset boundary. Folder `.` denotes the source root. Groups show immediate
supported-media contents, not recursive subtotals or a complete empty-folder tree;
empty/nonmedia-only directories are represented in the overall directory count.

The UI clearly shows COMPLETE/INCOMPLETE coverage, separate counters, the captured
source root/identity, and future eligibility. Non-snapshot history shows no manifest
and is explicitly NOT eligible. Unknown/live owner states remain RUNNING with locks
retained. This is review only: no snapshot diffing, search engine, reorganization,
destination preflight, Keep/Copy/Move actions, or rename UI was added.

Manifest insertion is database-side. Useful indexes cover scan observation IDs,
source snapshot history, snapshot manifest pages, snapshot folder groups, and optional
asset-reference lookup. No speculative content-hash or media-analysis indexes exist.
The single finalization transaction may be substantial for a huge manifest, but runs
off the UI thread and never allocates the whole result in Python.

## Interrupted scans and ownership

Each new scan records its Windows PID and process creation-time token. A dedicated
read-only process boundary uses [OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess),
[GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes),
and a zero-timeout [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)
to distinguish a live owner, an ended owner, and PID reuse. It requests query/synchronize
access only and always closes handles. It never terminates or modifies a process.

Startup and explicit history review inspect RUNNING sessions in bounded pages. A
provably ended/reused owner becomes INTERRUPTED; a live or uncertain/access-denied
owner is left untouched. Conditional updates recheck status/ownership transactionally
to avoid racing normal completion. Committed counters/observations/issues remain;
a recovery issue and interruption time are added. Missing flags and reconciliation
are untouched, no snapshot is created, and traversal is not resumed. Source and global
RUNNING lock semantics are released for classified interrupted sessions.

Migration 3's status CHECK remains unchanged. To preserve that schema safely without
rewriting historical migrations or rebuilding FK tables, storage uses FAILED plus
`interrupted_at`; the `scan_history` view and services expose the distinct effective
state **INTERRUPTED**. Coverage/identity authority is cleared. Normal failure remains
FAILED without this marker. Repeated recovery is idempotent.

Ownerless RUNNING rows from M4 cannot participate in M5 observation capture and are
classified interrupted after upgrade. Close old binaries before upgrading; concurrently
running a pre-M5 binary cannot provide the new ownership evidence. Unknown new-process
ownership never causes automatic lock release. Recovery performs no source filesystem
access, no missing reconciliation, and no automatic retry/resume.

## Migration and retention

Migration 4 adds observation/snapshot/manifest tables and guards; ownership, captured
source identity, sealed/format flags, identity verification, unsupported counts,
interruption markers, and snapshot error fields; and a history view. It contains no
filesystem or process access. Recovery is a separate explicit runtime lifecycle step.
Migrations 1–3 were not edited. Existing IDs, identity values, settings, assets/metadata,
scan counters, and issues are preserved. No legacy manifest is fabricated or scan
silently deleted. Checksums and migration history remain compatible.

Snapshots, manifests, and observations are retained without automatic deletion. This
duplicates scan-time metadata and increases database storage per scan; no retention
or compaction policy is implied. Future cleanup requires deliberate design preserving
historical integrity. Snapshot data remains historical even after current assets change.

## Files added

- `app/database/migrations/v004_snapshots.py`
- `app/database/repositories/snapshots.py`
- `app/services/process_ownership.py`, `app/services/snapshots.py`
- `app/ui/snapshot_review.py`
- `tests/test_snapshots.py`, `test_snapshot_recovery.py`, `test_snapshot_large.py`,
  `test_snapshot_ui.py`, `test_snapshot_safety.py`
- `docs/M5_SCAN_SNAPSHOT_MANIFEST.md`

## Files changed

- `app/database/migrations/__init__.py`: register Migration 4.
- `app/database/repositories/scans.py`: capture ownership/source context, observations,
  unsupported counts, sealing/verification, and effective interrupted state.
- `app/services/scanner.py`: capture unsupported totals and finalize after operational
  completion; build failure preserves the completed scan and its observations.
- `app/database/repositories/sources.py`, `app/services/sources.py`: effective history
  state and meaningful retained-history removal refusal.
- `app/main.py`: explicit startup recovery and snapshot-service wiring.
- `app/ui/main_window.py`, `app/ui/source_page.py`: review entry point and build-error
  visibility; existing explicit scan/cancel actions remain.
- `README.md`: creation, coverage, review, recovery, retention, and test instructions.

## Tests run

Environment: Windows, Python 3.13.15, PySide6/Qt 6.12.0, pytest 9.1.1, pytest-qt 4.5.0.

- Complete suite: `.venv/Scripts/python.exe -m pytest -q` — **154 passed in 38.20 s**.
- `python -m compileall -q app tests` — passed.
- `python -m pip check` — passed.
- `python -m app --help` — passed without scanning or snapshot construction.
- UI boundary search — no raw SQL, ctypes, filesystem traversal/stat, or direct
  repository access found in UI modules.

Tests use temporary databases/media fixtures and synthetic database observations;
no real user media was scanned.

Coverage includes complete/incomplete/ineligible results, exact counts/bytes and types,
manifest/folder/history paging, finalized immutability, issue retention, asset/source
mutation independence, second scans, cancelled/failed runs, path-conflict observations,
build rollback after partial INSERT, database-only retry after a newer scan, real M4
upgrade, retained empty snapshots, and all existing M1–M4 regression checks.

Recovery tests cover false/live/unknown owner probes, actual Windows current/exited
processes, PID reuse, ownerless legacy upgrade, preserved counts and missing flags,
lock release, no snapshot for interruptions, and repeated native startup idempotence.

A 12,000-row synthetic observation fixture creates the manifest using INSERT ... SELECT,
checks exact aggregates and bounded keyset pages, and asserts Python allocation peak
below 8 MB during finalization. It creates no large real filesystem tree. Qt tests
verify database reads run off the UI thread, timers keep responding during delayed
reads, manifest/history pages remain bounded, and retry refreshes the review correctly.

The subprocess snapshot-build audit forbids enumeration, source stat/lstat/media opens,
media mutation, hashing, subprocesses, and network activity. Finalization and bounded
manifest/folder review still succeed from persisted observations. Test media bytes and
tree contents remain unchanged. M4's original scanner audit also remains in the suite.

## Manual validation, safety/regression, and known limitations

No human visual inspection or physical drive-remount test was performed. Native Qt
startup/shutdown, repeated recovery, review responsiveness, and scanner lifecycle are
validated automatically. No million-row throughput benchmark is claimed; the larger
test validates database-side construction and bounded Python/UI allocation.

The scanner remains extension/stat-only; M5 introduces no media decoding, dimensions,
video metadata, hashing, FFmpeg/ffprobe, thumbnails, duplicates, ComfyUI, or user-file
modification. Snapshot eligibility is a future-input property and grants no file-change
authorization. All schema/history writes remain in application-owned SQLite storage.

Stored snapshots are immutable historical observations, not a guarantee that live
files remain unchanged. M3/M4 path fallback, reparse-race, and native I/O cancellation
limitations remain. Unknown owner checks conservatively retain locks. Legacy completed
M4 scans require a fresh explicit scan, and old binaries must be stopped for upgrade.
Storage grows without automatic pruning. Full snapshot diffing, reorganization,
metadata extraction, and thumbnails remain outside M5.

Recommended next milestone: **M6 — Image Metadata**, only on explicit instruction.
