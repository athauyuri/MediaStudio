# Codex Master Instructions — Local Media Studio v2

## Source of Truth

Before changing code, read:

```text
MEDIA_STUDIO_PROJECT_PLAN.md
MEDIA_STUDIO_DESIGN_PLAN.md
CODEX_MASTER_INSTRUCTIONS.md
```

The project plan defines scope/milestones.
The design plan defines architecture and safety behavior.
This file defines implementation discipline.

---

# 1. Mandatory Architecture

This is a native Python desktop app.

Use:

```text
Python 3.13
PySide6 / Qt 6
SQLite WAL
FFmpeg / ffprobe
watchdog
ComfyUI REST/WebSocket
```

Do NOT create:

- React
- FastAPI internal backend
- browser UI
- internal REST API
- Electron/Tauri
- local media HTTP proxy

UI calls Python services directly.

HTTP/WebSocket is reserved for ComfyUI or another truly separate local service.

---

# 2. Work One Milestone at a Time

For every milestone:

1. inspect repository
2. read relevant docs
3. implement only that milestone
4. add/update tests
5. run tests
6. perform regression checks
7. update docs
8. provide completion report
9. stop

Do not automatically continue.

---

# 3. Filesystem Safety

Never:

- silently overwrite
- automatically delete
- move without explicit plan
- rename without preview
- auto-delete duplicates
- delete source before destination verification

All file-changing workflows:

```text
analyze
-> preview
-> validate
-> persist plan
-> execute
-> verify
-> audit
```

---

# 4. Reorganization Requirements

No reorganization may start without:

- completed scan
- saved snapshot/manifest
- counts and total bytes
- user-selected folder/group actions
- duplicate/conflict analysis
- destination mapping
- free-space preflight
- safety-reserve validation
- persisted execution plan

User actions per folder/group:

```text
Keep
Index Only
Copy
Move
Exclude
```

---

# 5. Disk-Space Preflight

Before Copy or cross-volume Move:

1. resolve duplicates
2. resolve conflicts
3. resolve final destinations
4. calculate required bytes per destination volume
5. read free space
6. apply safety reserve

Do not start when:

```text
free < required + reserve
```

Show:

- required
- available
- reserve
- shortfall

Same-volume move must not be incorrectly charged as a full duplicate copy.

Recheck free space during long operations.

---

# 6. Cross-Volume Move Algorithm

Mandatory:

```text
COPY
-> VERIFY
-> DELETE SOURCE
```

Source deletion is forbidden until verification succeeds.

---

# 7. Validation

Every reorganization batch records/shows:

- expected count
- expected bytes
- successful count
- failed count
- skipped count
- destination count
- destination bytes
- verification result

Count alone is insufficient.

Cross-volume move requires hash verification before source deletion.

---

# 8. Persistent Journal

Persist every operation state.

Suggested states:

```text
PLANNED
COPYING
COPIED
VERIFYING
VERIFIED
SOURCE_REMOVING
SOURCE_REMOVED
RENAMING
RENAMED
COMPLETED
FAILED
SKIPPED
```

On restart, reconcile journal state with actual filesystem state.

Do not blindly rerun everything.

---

# 9. UI Responsiveness

Never run long/blocking operations on the Qt UI thread.

Workers handle:

- scanning
- metadata
- thumbnails
- ffprobe/FFmpeg
- hashing
- copy/move
- rename batches
- verification
- ComfyUI monitoring
- large DB work when appropriate

A worker failure must not freeze or terminate the application.

---

# 10. Error Handling

Use structured errors.

User message must explain:

- what failed
- source
- destination if relevant
- understandable reason
- whether original remains safe
- available actions

Default Move error behavior:

```text
pause batch
show error
offer Retry / Skip / Stop / Open Location
```

Explicit categories:

```text
FILE_IN_USE
ACCESS_DENIED
SOURCE_MISSING
DESTINATION_EXISTS
INSUFFICIENT_SPACE
DEVICE_OFFLINE
READ_ERROR
WRITE_ERROR
HASH_MISMATCH
INVALID_NAME
UNKNOWN
```

Never silently swallow an exception.

---

# 11. Large Library Rules

Assume 1,000,000+ assets.

Therefore:

- no full asset-table load
- no widget-per-asset
- no million-object queue
- use Qt Model/View
- page DB data
- lazy thumbnails
- bounded memory cache
- chunk scans/writes
- use DB indexes
- avoid O(n²) library-wide logic

---

# 12. Cache Rules

Allowed:

- thumbnails
- video poster frames
- optional video proxies

Do not cache/proxy originals merely for viewing.

Read original images/videos directly from local paths.

---

# 13. Database Rules

Use SQLite WAL and versioned migrations.

Never delete/rebuild the user database to upgrade schema.

Use transactions for critical state changes.

---

# 14. Code Boundaries

Prefer:

```text
app/
  ui/
  models/
  services/
  database/
    migrations/
    repositories/
  workers/
  utils/
```

UI code must not contain core filesystem business logic.

---

# 15. Source Rules

Track volume identity where practical.

If a source goes offline:

- keep assets
- keep thumbnails
- mark offline
- never automatically delete records

---

# 16. Scan Rules

Scan must:

- run in background
- report progress
- tolerate access-denied folders
- commit in chunks
- create scan session
- create snapshot/manifest
- expose counts/bytes

Reorganization consumes the completed snapshot.

---

# 17. Rename Rules

Flow:

```text
plan
-> preview
-> validate
-> execute
-> verify
-> journal
```

Support initially:

- prefix
- suffix
- fixed text
- original filename
- running number

Never overwrite an existing file.

Handle collisions/cycles safely.

---

# 18. Duplicate Rules

Initial duplicate detection is exact-content only.

Do not auto-delete.

Compute full hashes lazily when possible.

---

# 19. ComfyUI Rules

Default:

```text
http://127.0.0.1:8188
```

Do not import ComfyUI/Torch/custom nodes into Media Studio.

Use REST/WebSocket for ComfyUI only.

Index outputs directly from filesystem.

---

# 20. Persistent Batch Rules

SQLite is source of truth.

Support restart recovery.

No arbitrary application-level batch limit.

For huge selections:

- query-based lazy batches, or
- chunked materialization

Never create one million Python objects at once.

---

# 21. Testing

Every milestone adds appropriate tests.

Critical tests include:

- migrations
- Windows paths
- scan counts
- disk-space calculation
- same-volume/cross-volume classification
- conflict analysis
- copy/verify/delete state machine
- recovery logic
- rename planning/conflicts
- batch transitions
- worker error propagation

Use temporary directories for destructive file-operation tests.

Never test destructive logic against real user folders.

---

# 22. Regression Checks

Before completion:

- app launches
- main window responds
- migrations pass
- tests pass
- previous features still work
- no blocking work was introduced on UI thread
- read-only workflows do not unexpectedly modify files

---

# 23. Scope Control

Do not implement unless explicitly requested:

- cloud services
- authentication
- multi-user
- React/FastAPI
- semantic search
- face recognition
- OCR
- video editor
- auto duplicate deletion
- Character Studio migration
- DaVinci integration

---

# 24. First Milestone

Start only with:

## M1 — PySide6 Application Skeleton

Create:

```text
app/
tests/
docs/
scripts/
data/
cache/
config/
logs/
```

Implement:

- Python package structure
- PySide6 main window
- navigation shell
- configuration loader
- rotating logging
- worker infrastructure skeleton
- clean startup/shutdown
- basic automated tests
- README startup instructions

Do NOT implement yet:

- media DB schema
- scanner
- thumbnails
- reorganization
- rename
- ComfyUI
- media browser

Acceptance:

- app launches on Windows
- main window stays responsive
- logging initializes
- config loads
- tests pass

Stop after M1.

---

# 25. Required Completion Report

```text
Milestone:
Status:

Implemented:
- ...

Files added:
- ...

Files changed:
- ...

Tests run:
- ...

Manual validation:
- ...

Safety/regression validation:
- ...

Known limitations:
- ...

Recommended next milestone:
- ...
```

Do not simply say "done".
