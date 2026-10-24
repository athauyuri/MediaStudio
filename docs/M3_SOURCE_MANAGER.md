# M3 — Source Manager

Milestone: M3 — Source Manager  
Status: complete, 2026-10-24. M2 was accepted before this work. The three root
planning documents were reread completely. M4 Scanner has not started.
M1/M2 reports remain historical records of their milestone-time behavior.

## Implemented

The native **Sources** navigation page provides Add Source, Refresh Status,
Enable / Disable, Rename Display Name, and Remove Source. It uses a native folder
picker and a Qt table model, with bounded pages of 100 source registrations.
The table shows name, current path, status, volume/drive, enabled state, last seen,
and last scan. No scan is triggered by any source operation.

**Registering a source does not scan it.**

All repository and volume operations triggered by the page run through SourceService
in background workers. UI widgets contain no SQL, filesystem inspection, or ctypes
calls. Worker signal handlers are connected before execution starts, avoiding missed
completion signals for fast operations. Controls disable during an operation; the
Qt event loop and other navigation remain responsive.

## Identity and Windows path policy

`app/utils/windows_paths.py` is the central, pure lexical normalizer. It accepts
fully qualified drive and UNC paths, converts slashes, collapses lexical dot segments,
removes redundant trailing separators while preserving roots, and uppercases the
display drive letter. Folder-name casing and Unicode display text are preserved.
Separate comparison keys use Unicode casefold, conservatively treating case variants
as the same source. Extended drive/UNC prefixes normalize to their conventional forms.
Relative/drive-relative paths, device namespaces, NUL, unsupported characters, and
ambiguous trailing dots/spaces are rejected. No realpath/resolve or directory traversal
is used. This normalization is part of Migration 2 history; future policy changes
must retain it and introduce a versioned policy/migration.

With stable volume metadata, identity is:

```text
volume:<casefolded volume GUID>|<casefolded volume-relative root>
```

The stable database source ID remains unchanged if a path, display name, enable state,
or drive letter changes. Display/current paths are independent of the identity key.
Volume serial is retained as informational metadata, never used as a unique identity.
Volume labels are not used.

The ctypes boundary uses documented Windows APIs:

- [GetVolumePathNameW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumepathnamew)
  identifies the root's volume mount.
- [GetVolumeNameForVolumeMountPointW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumenameforvolumemountpointw)
  provides a volume GUID path.
- GetVolumeInformationW reads optional serial metadata.
- [GetVolumePathNamesForVolumeNameW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumepathnamesforvolumenamew)
  resolves mounts for one stored GUID; it does not enumerate media or scan drives.

When a GUID is unavailable, identity is `path:<normalized root>`. The UI explicitly
shows **Identity unresolved** for an accessible fallback source, with a limitation
message. Such a source is not claimed to survive a drive-letter change. Refresh may
enrich it with a GUID when available. Missing roots cannot be newly registered;
an already registered source is retained when it becomes inaccessible.

Selected junction/symlink/reparse roots are rejected after a non-following stat of
the root itself; the user must select the target explicitly. There is no recursive
link resolution. Ancestor aliases are not canonicalized into physical paths, and
different ancestor junction aliases are not guaranteed to deduplicate. If the root
cannot safely be related lexically to a returned volume mount, the resolver uses an
explicit path fallback. Full reparse traversal policy remains M4 scope.

## Registration, duplicates, and nested roots

Registration validates only the selected root and necessary volume metadata, calculates
identity, then atomically checks duplicates and persists the source. Path variants and
matching GUID/relative-root identities are rejected with the existing source's name
and path. The database has a partial unique identity index with NOCASE collation;
service keys are already casefolded for Unicode comparisons. A separate normalized
root lookup also prevents registering the same display root under a different identity.
Concurrent service registrations serialize duplicate checks with BEGIN IMMEDIATE.

Nested sources are allowed after explicit UI confirmation. Both ancestor and descendant
overlaps are detected, including roots represented by different mount letters on the
same known volume. The warning is checked before persistence and is rechecked on the
confirmed registration attempt. Sibling names that merely share a text prefix do not
count as overlaps. There is no filesystem inspection to determine nesting.

## Status, remounts, and removal

Status reflects the last saved check; Refresh Status explicitly checks roots on the
displayed page. Opening the application/page only reads stored registrations, without
automatically probing volumes or media. GUID-backed refresh queries mounts for the
expected GUID, joins only the stored relative root, and verifies the resulting GUID
and relative identity before updating current path, mount, online state, and last seen.
An unrelated volume now occupying the old drive letter is never silently adopted.

Offline/error checks retain registration, stable identity, last successful seen time,
and all indexed asset records. Messages are shown in the page and per-row tooltips.
Disabled status takes precedence visually; disabling changes only database state.
The repository's eligible-source query excludes disabled sources for future consumers.

Renaming changes only the display name. Removal requires confirmation, removes only
the database registration, and never deletes the folder or media. An atomic reference
check rejects removal if assets exist; the original database foreign-key restriction
also remains in force. No cascade, detach, or automatic cleanup was added.

## Migration 2

Migration 1 was not edited. Migration 2 adds normalized root, GUID, serial,
volume-relative root, source identity key, last seen, and status message columns,
plus identity/root lookup indexes. It performs only deterministic lexical backfill;
no root, mount, or media is touched during upgrade.

Legacy unique valid roots receive path fallback identities. Invalid legacy paths and
case-variant duplicate groups retain every row and ID, with null logical identities
and a review message. Existing settings/assets and foreign-key references survive.
Conflicting enrichment is reported without merging or deleting legacy rows. Repeated
initialization safely skips applied migrations and preserves history/checksums.

## Files added

- `app/utils/windows_paths.py`
- `app/database/migrations/v002_source_identity.py`
- `app/services/__init__.py`, `source_errors.py`, `sources.py`, `windows_volumes.py`
- `app/ui/source_page.py`
- `tests/conftest.py`, `test_sources.py`, `test_source_ui.py`,
  `test_windows_volumes.py`, `test_source_filesystem_safety.py`
- `docs/M3_SOURCE_MANAGER.md`

## Files changed

- `app/database/migrations/__init__.py`: register Migration 2 and pure path backfill function.
- `app/database/repositories/sources.py`: atomic identity/overlap checks, enrichment,
  enabled-source pages, and safe registration removal.
- `app/workers/manager.py`: optional signal configuration before task start.
- `app/ui/main_window.py`, `app/main.py`: Sources navigation and service wiring.
- `tests/test_database.py`, `tests/test_database_startup.py`: migration counts and
  injected next-version failure now account for Migration 2; prior guarantees retained.
- `README.md`: source management instructions, policies, scope, and test commands.

## Tests run

Environment: Windows, Python 3.13.15, PySide6/Qt 6.12.0, pytest 9.1.1, pytest-qt 4.5.0.

- Complete suite: `.venv/Scripts/python.exe -m pytest -q` — **87 passed**.
- `python -m compileall -q app tests` — passed.
- `python -m pip check` — passed.
- `python -m app --help` — passed without database/source initialization.
- UI boundary search found no SQL, ctypes, or filesystem traversal/inspection calls.

Coverage includes slash/case/trailing separator/Unicode/root/UNC paths, duplicate
variants, concurrent duplicate prevention, stable remount identity, wrong-volume
rejection, root registration, rename, enable/disable, reference-safe removal, online
and offline retention, fallback enrichment, nested/sibling/alias-root warnings, invalid
paths, missing/access errors, Windows API metadata, reparse rejection, and disconnected
mount handling. Real Migration 1 upgrade retains settings, assets, source IDs, duplicate
legacy rows, and Migration 1 history; repeated initialization and integrity/FK checks pass.

Qt tests exercise source rendering, native-picker handoff (mocked selection), every
management action, bounded pages, duplicate messages, accepted/declined nesting,
referenced-removal refusal, and timer responsiveness during a deliberately delayed
resolver call. Thread identity confirms the slow call occurs outside the UI thread.
Removable-drive changes are simulated deterministically; no physical removable drive
is required. Existing M1/M2 startup, import safety, workers, config, logging, and
no-automatic-media-access tests continue to pass.

## Manual validation

No human visual inspection or physical unplug/remount test was performed. Existing
native Windows Qt startup/shutdown subprocess checks pass. The new filesystem audit
uses the real Windows resolver against a temporary test root containing a nested
image fixture: registration and status refresh succeed while an audit hook forbids
source file opens, enumeration, creation, deletion, and renames, and a guarded stat
rejects all child-path inspection. Only root stat and volume metadata are allowed.
The fixture remains byte-for-byte unchanged; safe removal leaves its tree intact.

## Safety/regression validation and known limitations

Only source registrations and application-owned DB/log files are changed. No recursive
enumeration, media reads, counts, metadata extraction, media hashing, thumbnails,
FFmpeg/ffprobe, scan sessions, ComfyUI calls, or reorganization/file operations exist.
M3 adds no general persistent batch engine or new external dependency.

Native Windows metadata calls can be delayed by the operating system. They run off
the UI thread; cooperative cancellation takes effect between calls, and the existing
shutdown path waits for active workers rather than forcibly terminating an OS call.
Fallback paths and ancestor aliases have the identity limits described above. Legacy
duplicate resolution and deliberate detach/archive workflows are deferred.

Recommended next milestone: **M4 — Scanner**, only on explicit instruction.
