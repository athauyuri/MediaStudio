# Local Media Studio — Design Plan v2

## 1. Design Objective

Build a high-performance native Windows media application optimized for:

- direct filesystem access
- low UI latency
- bounded RAM use
- safe high-volume file operations
- recoverability
- clear before/after validation
- scalable browsing
- local ComfyUI integration

---

# 2. Architecture Decision

Selected:

```text
Python 3.13
PySide6 / Qt 6
SQLite
FFmpeg / ffprobe
watchdog
ComfyUI REST/WebSocket
```

Rejected for internal application use:

```text
React -> HTTP -> FastAPI -> filesystem
```

Reason:

- unnecessary serialization
- extra API layer
- more moving parts
- more caching complexity
- no advantage for direct local-drive operations

ComfyUI remains a separate process, so its localhost API is retained.

---

# 3. Process / Worker Model

```text
Qt Main UI Thread
    |
    +-- lightweight UI/state work
    |
    +-- Worker Manager
          |
          +-- scanner workers
          +-- metadata workers
          +-- thumbnail workers
          +-- hashing workers
          +-- file-operation workers
          +-- validation workers
          +-- ComfyUI monitor workers
```

The UI thread must never perform long-running filesystem work.

---

# 4. Service Boundaries

Recommended services:

```text
SourceService
ScanService
AssetService
ThumbnailService
PreviewService
SearchService
CollectionService
DuplicateService
ReorganizationService
RenameService
DiskSpaceService
ValidationService
JournalService
ComfyUIService
WorkflowService
BatchService
ActivityService
```

UI components call services; they do not perform file moves/copies themselves.

---

# 5. Qt Media Grid Design

Use:

```text
QListView
QAbstractListModel
QStyledItemDelegate
paged SQLite query
```

Do not create a QWidget per media file.

The model loads only a bounded working set.

Thumbnail decode is asynchronous.

Use a bounded LRU memory cache.

---

# 6. Scan Session Design

Each scan creates:

```text
scan_sessions
scan_manifest_entries
```

Session fields include:

```text
scan_id
source roots
status
start/end
folder_count
file_count
image_count
video_count
total_bytes
unreadable_count
missing_count
```

Manifest entries include:

```text
path
size
mtime
media_type
quick_hash
full_hash when needed
```

A completed scan is the fixed baseline for subsequent reorganization planning.

---

# 7. Reorganization UX

Recommended wizard/workspace:

## Step 1 — Select Sources
Choose drives/folders.

## Step 2 — Scan
Live progress in background.

## Step 3 — Review
Show counts, bytes, media breakdown, unreadable items, duplicates.

## Step 4 — Assign Actions
Each folder/group:

```text
Keep
Index Only
Copy
Move
Exclude
```

Parent choices may be inherited; children can override.

## Step 5 — Destination Mapping
Choose destination roots/groups.

## Step 6 — Preflight
Show conflicts, duplicates, required space, free space, reserve.

## Step 7 — Execute
Only enabled after all mandatory checks pass.

## Step 8 — Verify
Count, bytes, per-file state, hashes where required.

## Step 9 — Report
Persistent final report.

---

# 8. Reorganization Plan Model

Convert UI choices into a persisted plan before execution.

Concept:

```text
ReorgPlan
  batch_id
  scan_id
  destination settings
  safety reserve
  items[]
```

Each item:

```text
source_path
destination_path
operation_type
source_size
source_hash
same_volume
verification_policy
conflict_policy
```

Execution must only operate from the saved plan.

---

# 9. Disk-Space Calculation Design

Order:

```text
resolve actions
-> resolve duplicate reuse
-> resolve conflicts
-> resolve final destination paths
-> calculate bytes per destination volume
-> query free space
-> apply safety reserve
```

Multiple destination volumes are checked independently.

Example:

```text
D:
Required 1.42 TB
Free 1.78 TB
Reserve 100 GB
PASS

E:
Required 420 GB
Free 390 GB
Reserve 50 GB
FAIL
```

Start remains disabled until all destination volumes pass.

---

# 10. File Operation State Machine

Cross-volume move:

```text
PLANNED
-> COPYING
-> COPIED
-> VERIFYING
-> VERIFIED
-> SOURCE_REMOVING
-> SOURCE_REMOVED
-> COMPLETED
```

Same-volume move:

```text
PLANNED
-> RENAMING
-> RENAMED
-> VERIFYING
-> COMPLETED
```

Errors:

```text
FAILED
SKIPPED
```

Never mark Completed before verification.

---

# 11. Verification Design

## Level 1
Expected count vs successful count.

## Level 2
Expected bytes vs destination bytes.

## Level 3
Destination existence and expected size.

## Level 4
Hash verification.

For cross-volume move, hash must pass before source deletion.

Use SHA-256 initially unless later profiling justifies a different strategy.

---

# 12. Error Design

Workers return structured errors:

```text
error_code
category
user_message
technical_message
source_path
destination_path
retryable
suggested_actions
exception_type
```

Categories:

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

UI response:

```text
pause relevant batch
show exact file/reason
offer Retry / Skip / Stop / Open Location
```

No tight-loop retry.

No app-wide freeze.

---

# 13. Runtime Disk-Space Monitoring

Recheck available space:

- every configurable number of files
- every configurable number of copied GB
- before unusually large files

If reserve would be violated:

- pause
- preserve source
- show remaining requirement
- await user action

---

# 14. Recovery Design

At startup:

1. find incomplete reorganization batches
2. inspect journal states
3. inspect actual source/destination existence
4. reconcile state
5. offer Resume / Review / Cancel Recovery

Examples:

- `VERIFIED` + both files exist: destination is safe; source cleanup can resume.
- `COPYING`: partial destination is not trusted; verify or restart according to policy.
- `SOURCE_REMOVED` + destination verified: finish journal state without recopying.

---

# 15. Rename Design

Rename planning is pure and non-destructive.

Input:

```text
selected assets
template
start number
increment
replacement rules
```

Output:

```text
old path
new path
conflict result
validation result
```

No file changes happen until plan validation succeeds.

Use temporary-name staging where necessary to safely handle rename cycles/swaps.

---

# 16. Thumbnail / Preview Design

Thumbnail pipeline:

```text
asset
-> cache-key/version check
-> scaled decode
-> WebP thumbnail
-> cache metadata update
```

Original is never copied to cache.

Video playback uses original local path.

Optional video proxy is opt-in/configurable.

---

# 17. ComfyUI Design

Keep ComfyUI isolated.

```text
Media Studio
-> REST prompt submission
-> WebSocket progress
-> output detected on filesystem
-> index output directly
```

Store:

```text
workflow JSON
parameter mapping
source asset
prompt
seed
job state
output asset
```

---

# 18. Batch Design

SQLite is the source of truth.

For very large batches:

- persist selection query
- lazily claim next assets
- materialize manageable chunks only

Worker concurrency is configurable and conservative by default.

---

# 19. Performance Rules

- do not full-hash every file during ordinary scan
- compute full hashes lazily when duplicate detection/verification requires them
- chunk DB writes
- bound worker concurrency
- bound memory cache
- never decode full-resolution originals for media grid
- never preload enormous folder trees into widgets
- use indexes for repeated queries

---

# 20. Suggested Main Navigation

```text
Library
Collections
Reorganize
Rename
Duplicates
Generate
Batch Jobs
Activity
Settings
```

---

# 21. Final Reorganization Report

Store/display:

```text
batch id
scan id
start/end
initial file count
initial bytes
kept
indexed only
copied
moved
excluded
skipped
failed
destination count
destination bytes
count verification
byte verification
hash verification
errors
overall status
```

Overall status must distinguish:

```text
VERIFIED
COMPLETED WITH ERRORS
FAILED
CANCELLED
```

---

# 22. Design Priority Order

When tradeoffs occur:

1. prevent data loss
2. preserve UI responsiveness
3. give clear status/errors
4. support recovery
5. scale to large libraries
6. minimize unnecessary copying/cache
7. keep architecture simple
