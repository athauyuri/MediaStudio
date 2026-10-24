# M1 — PySide6 Application Skeleton

Status: complete, 2026-10-24. Scope follows all three root planning documents.
M2 has not started.

## Implemented

- Python 3.13 package and module/console entry points, project metadata and test setup.
- PySide6 main window with keyboard-accessible navigation and nine placeholder pages.
- Immutable validated TOML configuration, missing-file defaults, explicit startup errors.
- Application-owned UTF-8 rotating logs with bounded backups and handler cleanup.
- Private bounded QThreadPool, progress/result/error/cancel/finished signals,
  cooperative cancellation, and nonblocking close while outstanding workers finish.
- README and PowerShell launcher; required application/runtime directory structure.

UI only handles presentation and lifecycle. Worker infrastructure accepts callables;
it contains no scanning or file-operation business logic. Worker error payloads carry
category, user/technical messages, optional paths, exception type, and suggested actions.
Failures are logged and isolated; no automatic retry occurs.

## Files

Added `pyproject.toml`, `.gitignore`, `README.md`, `config/default.toml`,
`scripts/run.ps1`, this report, and:

- `app/__init__.py`, `app/__main__.py`, `app/main.py`, `app/config.py`
- `app/ui/{__init__,main_window}.py`
- `app/utils/{__init__,logging}.py`
- `app/workers/{__init__,manager}.py`
- `tests/test_config_logging.py`, `tests/test_shell_workers.py`, `tests/test_startup.py`
- `.gitkeep` markers under models, services, database/migrations, database/repositories,
  data, cache/thumbnails, cache/video-posters, cache/video-proxies, and logs.

Existing planning documents were read and preserved. Existing empty backend/frontend
directories were left untouched and are not part of the application.

## Validation

Environment: Windows, Python 3.13.15, PySide6/Qt 6.12.0, pytest 9.1.1,
pytest-qt 4.5.0. Local virtual environment `.venv`.

- `python -m pytest -q`: **16 passed**.
- Tests cover config defaults/validation/preservation, bounded log rotation and
  handler reinitialization, all navigation pages, worker thread identity/progress,
  structured failure isolation, continued worker use after failure, queue capacity,
  pending cancellation, responsive cooperative shutdown, and submission rejection
  during shutdown.
- Subprocess smoke test exercises the real entry point on the Windows Qt platform,
  displays the native window, schedules close, verifies exit code zero and both
  startup/shutdown log messages. No offscreen override was set for this validation.
- Invalid-config subprocess exits nonzero before creating runtime files.
- Compile check, CLI help, and dependency integrity check passed.

Manual visual inspection was not performed. Window launch, navigation, responsiveness,
and shutdown were validated through automated Qt tests rather than human inspection.

## Safety and regression

No database is created; migrations are not applicable until M2. No scan, media cache,
network listener, HTTP backend, media browser, rename, copy, move, deletion, or
ComfyUI integration exists. Runtime startup writes only application-owned logs.
Configuration loading is read-only. File-preservation tests use temporary directories.
Worker tests use synthetic tasks; no real media/source folders are touched.

## Limitations and next milestone

Navigation pages are placeholders. Workers require cooperative cancellation and
bounded I/O timeouts; shutdown deliberately waits without freezing the UI rather
than forcibly terminating a task. The small bounded queue is infrastructure for
M1, not a persistent batch engine or a logical batch-size limit. No task is submitted
automatically at startup. Packaging/installer work is deferred; use the documented
source/virtual-environment launch for now.

Recommended next milestone: **M2 — SQLite Foundation**, only on explicit instruction.
