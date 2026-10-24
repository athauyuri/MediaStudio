# Local Media Studio — Project Plan v2

## 1. Project Goal

Local Media Studio is a **native, local-first Windows desktop application** for organizing, previewing, searching, consolidating, renaming, generating, and batch-processing very large image and video collections stored across multiple local drives.

Primary problems to solve:

- Media is scattered across drives and folders.
- Windows Explorer becomes slow with large media folders.
- Media should be searchable without manually browsing drives.
- Selected media should optionally be consolidated into one managed library for easier backup and migration.
- Reorganization must be previewable, selective, verifiable, recoverable, and safe against accidental loss.
- Batch rename must support prefix, suffix, fixed text, running numbers, and reusable templates.
- The app should call local ComfyUI for image/video generation.
- Long-running work must not freeze the UI.
- Batch processing must persist across restarts and avoid arbitrary application-level limits.
- Initial scope remains focused on media management and generation, not recreating all Character Studio functionality.

---

# 2. Mandatory Architecture

## 2.1 Native Python Desktop App

Use:

- Python 3.13
- PySide6 / Qt 6
- SQLite with WAL
- FFmpeg / ffprobe
- Pillow and/or Qt image readers
- watchdog
- ComfyUI REST + WebSocket

Do **not** use React, FastAPI, an internal REST API, or a browser UI for the application itself.

The UI calls Python services directly.

## 2.2 Direct Filesystem Access

The app works directly with local paths via Python filesystem APIs.

Original images/videos remain at their real filesystem paths.

The app must not proxy original media through an HTTP layer.

## 2.3 Allowed Cache

Only derived preview data may be cached:

- image thumbnails
- video poster frames
- optional lightweight video proxies

Do not duplicate original media merely for viewing.

Caches must be safely regeneratable.

---

# 3. Product Safety Principles

## 3.1 Non-Destructive by Default

Scanning/indexing must never modify files.

File-changing operations require explicit user action:

- copy
- move
- rename
- consolidation
- duplicate cleanup

## 3.2 Reorganization Is Safety-Critical

No reorganization operation may start without:

1. scan
2. saved scan snapshot/manifest
3. preview of counts and size
4. user-selected action per folder/group
5. duplicate/conflict analysis
6. destination-space preflight
7. persisted execution plan
8. explicit Start

After execution:

9. verify results
10. show before/after validation
11. store final report

## 3.3 UI Must Never Freeze

Scanning, hashing, thumbnail creation, video processing, copy/move, validation, rename, and ComfyUI work must run outside the Qt UI thread.

Errors must affect only the relevant worker/job and must not hang the whole application.

---

# 4. Core Modules

1. Library
2. Source / Drive Manager
3. Media Scanner
4. Scan Snapshot / Manifest
5. Thumbnail / Video Poster Cache
6. Image / Video Preview
7. Search / Filters
8. Tags / Ratings / Favorites / Collections
9. Reorganization Planner
10. Managed Library / Consolidation
11. Rename Manager
12. Duplicate Detection
13. ComfyUI Integration
14. Generation Studio
15. Persistent Batch Manager
16. Activity / Audit History
17. Settings

---

# 5. Source and Drive Management

Support sources such as:

```text
C:\Users\<user>\Downloads
D:\Media
E:\ComfyUI\output
F:\Projects
G:\Archive
```

Track:

```text
source_id
name
root_path
volume_identifier
current_mount
enabled
online
last_scan
watch_enabled
```

Where practical, identify external drives by Windows volume identity rather than drive letter alone.

If a drive goes offline:

- retain indexed records
- retain thumbnails
- mark source offline
- do not delete assets automatically

---

# 6. Scanning

Support:

- full source scan
- incremental scan
- selected-folder scan
- rescan
- background watcher

Scanner requirements:

- recursive traversal
- supported media detection
- safe access-denied handling
- changed/missing file detection
- progress reporting
- cancellation
- chunked DB commits
- no UI blocking

Every completed scan creates an immutable snapshot.

Example:

```text
Scan ID: SCAN-20261024-001
Folders: 1,482
Files: 183,724
Images: 151,203
Videos: 31,882
Total Size: 2.41 TB
Unreadable: 3
Missing: 12
```

The user must be able to inspect the scan before any file operation.

---

# 7. Fast Media Browsing

## 7.1 Thumbnail Cache

Use cached thumbnails for gallery display, e.g.:

```text
256 px
512 px
1024 px
```

Do not decode full-resolution originals for gallery tiles.

Keep only a bounded number of decoded thumbnails in RAM.

## 7.2 Direct Full Preview

When opening an image, read the original directly from disk.

For very large images, decode a scaled preview first where practical.

## 7.3 Video

- use ffprobe for metadata
- generate poster frame optionally during indexing/background processing
- pass original local path directly to the video player
- optional proxy generation only when enabled

## 7.4 Large Library UI

Use Qt Model/View:

```text
QListView / QTableView
QAbstractListModel / QAbstractTableModel
delegates
paged SQLite queries
lazy thumbnail loading
```

Never create one QWidget per asset.

Design target: 1,000,000+ indexed assets.

---

# 8. Search and Organization

Search/filter by:

- filename
- media type
- source
- managed/external
- online/offline
- date
- size
- width/height
- orientation
- duration
- rating
- favorite
- tags
- collections
- workflow
- ComfyUI-generated status

Support:

- tags
- ratings 0–5
- favorites
- virtual collections

Collections must not duplicate physical files.

---

# 9. Managed Library

Support:

## Indexed Mode

Files remain where they already are.

## Managed Mode

Files are stored under a configured root, e.g.:

```text
D:\MediaLibrary\
    Images\
    Videos\
    Generated\
    Imported\
    Projects\
    _library\
```

The database remains the main organization layer; users should not need to manually browse this hierarchy.

---

# 10. Reorganization Planner

## 10.1 Required Workflow

```text
Select Sources
-> Scan
-> Save Snapshot
-> Review Scan Result
-> Assign Actions
-> Resolve Duplicates/Conflicts
-> Map Destinations
-> Check Disk Space
-> Preview Execution Plan
-> Start
-> Execute in Background
-> Verify
-> Final Report
```

## 10.2 Folder-Level Flexibility

After scanning, each folder/group can independently be assigned:

```text
Keep in Current Location
Index Only
Copy to Managed Library
Move to Managed Library
Ignore / Exclude
```

Allow:

- parent action inheritance
- child folder override
- multi-select
- individual-file override where necessary

Example:

```text
F:\OldMedia              Move
    Raw\                 Keep
    Yanxi\               Move
    Temporary\           Exclude
```

## 10.3 Scan Preview

Show:

- source path
- folder count
- file count
- image count
- video count
- total bytes
- unreadable count
- missing count
- duplicate candidates

Allow drill-down before any change.

---

# 11. Destination Disk-Space Preflight

Disk-space validation is mandatory before Copy or cross-volume Move.

Required order:

1. resolve selected actions
2. resolve exact duplicate reuse
3. resolve naming/path conflicts
4. calculate final destinations
5. calculate required bytes per destination volume
6. read available bytes
7. apply configured safety reserve
8. allow Start only if all destinations pass

## 11.1 Required-Space Rules

```text
Copy
-> full destination bytes required

Cross-volume Move
-> full destination bytes required
-> because implementation is copy -> verify -> delete

Same-volume Move
-> usually little/no duplicate payload required
-> safe rename/move semantics may be used

Existing verified duplicate reused
-> zero additional payload bytes
```

## 11.2 Safety Reserve

Configurable:

- 10 GB
- 25 GB
- 50 GB
- 100 GB
- percentage of destination drive
- custom

Rule:

```text
free_space >= required_space + safety_reserve
```

If not satisfied:

- disable Start
- change no files
- show required space
- show available space
- show reserve
- show shortfall
- provide Change Destination / Modify Selection / Cancel

## 11.3 Runtime Recheck

During long operations, periodically re-check free space.

If the reserve would be violated:

- pause operation
- do not delete unverified source files
- show meaningful message
- allow user to free space, change destination where supported, or stop

---

# 12. Safe Copy / Move Semantics

## 12.1 Cross-Volume Move

Mandatory algorithm:

```text
COPY destination
-> VERIFY destination
-> DELETE source
```

Never delete the source before successful verification.

## 12.2 Same-Volume Move

A safe filesystem rename/move may be used.

Still journal and validate the final destination.

## 12.3 Verification Levels

Use:

1. expected count
2. total bytes
3. destination existence/size
4. hash verification where safety requires

For safety-critical cross-volume moves, verify hash before source deletion.

Count alone is not sufficient.

---

# 13. Before / After Validation

Every reorganization batch must show a validation result.

Example:

```text
Before
Expected files: 12,483
Expected bytes: 197,423,918,221

After
Successful: 12,483
Failed: 0
Skipped: 0
Destination files: 12,483
Destination bytes: 197,423,918,221

Count Match: YES
Byte Match: YES
Hash Verification: PASS
Overall Status: VERIFIED
```

For Copy:

- verify destination
- verify source remains

For Move:

- source may be removed only after destination verification

---

# 14. Persistent Reorganization Journal

Every file-changing operation must have persistent state.

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

Store:

```text
operation_id
batch_id
scan_id
source_path
destination_path
source_size
expected_hash
operation_type
state
error_code
error_message
created_at
updated_at
```

On restart, inspect incomplete operations and reconcile actual filesystem state instead of blindly repeating the whole job.

---

# 15. Error Handling

## 15.1 Default Behavior

For a Move error:

```text
Pause current reorganization batch
Show meaningful message
Wait for user choice
```

Options:

```text
Retry
Skip File
Stop Operation
Open File Location
```

## 15.2 Meaningful Error Categories

Handle explicitly:

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

Example:

```text
Unable to move file.

Reason:
The file is currently in use by another application.

Source:
D:\Videos\project01.mp4

No source file has been deleted.

[Retry] [Skip] [Stop] [Open Location]
```

Any error must leave the main UI responsive.

Optional unattended skip-and-continue mode may be added later, but pause-on-error is the default for Move.

---

# 16. Rename Manager

Support reusable templates:

- prefix
- suffix
- fixed text
- original filename
- running number
- replace/remove text
- separators
- case conversion

Tokens may include:

```text
{original}
{number}
{date}
{time}
{character}
{project}
{workflow}
{width}
{height}
{resolution}
{seed}
```

Example:

```text
Yanxi_{number:05}
```

becomes:

```text
Yanxi_00001.png
Yanxi_00002.png
```

Mandatory flow:

```text
Build Plan
-> Preview
-> Validate
-> Execute
-> Verify
-> Audit
```

Detect:

- duplicate generated names
- existing targets
- invalid Windows names
- reserved names
- missing source
- path-length issues
- case-only conflicts

Provide safe undo when possible.

---

# 17. Duplicate Detection

Initial release uses exact-content hashes.

Show:

- duplicate groups
- paths
- sizes
- total duplicate usage
- potential savings

Never delete automatically.

Perceptual similarity is future scope.

---

# 18. ComfyUI Integration

Default:

```text
http://127.0.0.1:8188
```

Use:

- REST for prompt submission/history
- WebSocket for progress/events

Keep ComfyUI's Python/Torch/CUDA/custom-node environment separate from Media Studio.

Do not import ComfyUI internals directly.

Initial workflow types:

- T2I
- I2I
- I2V
- T2V
- Upscale

Store workflow JSON and parameter mappings rather than scattering node IDs throughout UI code.

Completed outputs should be indexed directly from filesystem.

---

# 19. Persistent Batch Engine

All batch state lives in SQLite.

Support:

- start
- pause
- resume
- cancel
- retry
- stop after current
- restart recovery

No arbitrary application-level batch limit.

For huge logical batches:

- create jobs in chunks, or
- store a query/selection and lazily claim the next items

Never create one million Python job objects at once.

---

# 20. Suggested Database Tables

```text
settings
sources
assets
asset_metadata
scan_sessions
scan_manifest_entries
tags
asset_tags
collections
collection_assets
thumbnails
video_previews
reorg_batches
reorg_operations
rename_batches
rename_operations
duplicate_groups
duplicate_group_assets
workflows
workflow_parameters
generation_jobs
batches
batch_jobs
activity_log
```

All schema changes use versioned migrations.

---

# 21. Suggested Repository Layout

```text
MediaStudio/
|
+-- app/
|   +-- main.py
|   +-- ui/
|   +-- models/
|   +-- services/
|   +-- database/
|   |   +-- migrations/
|   |   +-- repositories/
|   +-- workers/
|   +-- utils/
|
+-- data/
+-- cache/
|   +-- thumbnails/
|   +-- video-posters/
|   +-- video-proxies/
+-- config/
+-- logs/
+-- tests/
+-- docs/
+-- scripts/
|
+-- MEDIA_STUDIO_PROJECT_PLAN.md
+-- MEDIA_STUDIO_DESIGN_PLAN.md
+-- CODEX_MASTER_INSTRUCTIONS.md
```

---

# 22. Milestone Roadmap

## Phase 1 — Native Foundation

### M1 — PySide6 Application Skeleton
- Python package
- main window
- navigation shell
- config
- rotating logging
- worker infrastructure skeleton
- automated test setup

### M2 — SQLite Foundation
- connection
- WAL
- migrations
- settings/sources/assets tables

### M3 — Source Manager
- add/disable sources
- online/offline status
- volume identity where practical

### M4 — Scanner
- recursive scan
- progress/cancel
- chunked DB writes

### M5 — Scan Snapshot / Manifest
- immutable counts
- bytes
- manifest
- scan preview UI

## Phase 2 — Fast Browser

### M6 — Image Metadata
### M7 — Video Metadata / ffprobe
### M8 — Thumbnail Worker
### M9 — Video Poster Worker
### M10 — Virtualized Library Model/View
### M11 — Image Preview
### M12 — Video Playback
### M13 — Search / Filters

## Phase 3 — Organization

### M14 — Tags
### M15 — Collections
### M16 — Ratings / Favorites
### M17 — Offline Source Handling
### M18 — Exact Duplicate Detection

## Phase 4 — Safe Reorganization

### M19 — Managed Library Configuration
### M20 — Folder-Level Action Planner
### M21 — Reorganization Analysis
### M22 — Destination Disk-Space Preflight
### M23 — Persistent Reorganization Journal
### M24 — Safe Copy
### M25 — Safe Cross-Volume Move
### M26 — Same-Volume Move
### M27 — Error Pause / Retry / Skip / Stop
### M28 — Runtime Disk-Space Monitoring
### M29 — Before / After Validation
### M30 — Restart Recovery
### M31 — Final Reorganization Report

## Phase 5 — Rename

### M32 — Rename Rule Engine
### M33 — Rename Preview
### M34 — Rename Conflict Validation
### M35 — Batch Rename
### M36 — Rename Verification / Audit
### M37 — Rename Undo
### M38 — Advanced Tokens

## Phase 6 — ComfyUI

### M39 — Connection
### M40 — Workflow Registry
### M41 — Parameter Mapping
### M42 — T2I
### M43 — I2I
### M44 — I2V
### M45 — T2V
### M46 — Upscale
### M47 — Output Auto-Import
### M48 — Generation History

## Phase 7 — Persistent Batch Engine

### M49 — Batch State Machine
### M50 — Worker Queue
### M51 — Pause / Resume / Cancel
### M52 — Retry / Error Handling
### M53 — Restart Recovery
### M54 — Query-Based Large Batches
### M55 — Stress Testing

## Phase 8 — Future

Only after core stability:

- perceptual duplicate detection
- semantic search
- AI tagging
- face clustering
- OCR
- scene detection
- backup synchronization
- Character Studio integration
- installer/packaging improvements

---

# 23. Version 1 Success Criteria

Version 1 succeeds when the user can:

1. register multiple drives/folders
2. scan and inspect counts before file changes
3. browse very large collections quickly
4. preview images/videos directly from local paths
5. search/filter/organize media
6. selectively keep, index, copy, move, or exclude folders
7. see disk-space requirements before starting
8. block execution when space is insufficient
9. safely move cross-volume files via copy -> verify -> delete
10. compare before/after counts and bytes
11. receive a final verification report
12. recover safely from interrupted reorganization
13. receive meaningful errors without app freeze
14. batch rename with preview and undo
15. detect exact duplicates
16. connect to local ComfyUI
17. run image/video generation
18. automatically index outputs
19. run persistent large batches without arbitrary limits
