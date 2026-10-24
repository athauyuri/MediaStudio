"""Non-destructive filesystem scanner: directory entries/stat only, never open media."""
from datetime import datetime, timezone
import logging
import ntpath
import os
import stat
from threading import Event
from time import monotonic

from app.database.errors import DatabaseError
from app.database.repositories.scans import COUNTERS, ScanRepository
from app.services.media_types import media_type
from app.services.scan_errors import ScanError
from app.services.scan_policy import ScanPolicy
from app.services.source_errors import SourceError
from app.services.sources import identity_values
from app.services.snapshots import SnapshotService
from app.utils.windows_paths import normalize_windows_path, relative_path_key
from app.workers.manager import Cancelled


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def issue_for(path: str, error: Exception) -> dict:
    code = getattr(error, "error_code", None)
    if not code:
        code = "ACCESS_DENIED" if isinstance(error, PermissionError) else "DIRECTORY_NOT_FOUND" if isinstance(error, FileNotFoundError) else "PATH_ACCESS_ERROR" if getattr(error, "winerror", None) == 206 else "IO_ERROR" if isinstance(error, OSError) else "UNKNOWN"
    return dict(path=path, category=code,
        user_message=getattr(error, "user_message", None) or f"Unable to inspect this path ({code}). No media files were changed.",
        technical_message=str(error))


class Filesystem:
    """Injectable traversal boundary for deterministic inaccessible/deep-tree tests."""
    @staticmethod
    def entries(path):
        return os.scandir(path)

    @staticmethod
    def directory_stat(path):
        return os.stat(path, follow_symlinks=False)


class ScanService:
    def __init__(self, repository: ScanRepository, sources, policy: ScanPolicy,
                 filesystem=None):
        self.repository, self.sources, self.policy = repository, sources, policy
        self.filesystem = filesystem or Filesystem()
        self.snapshots = SnapshotService(repository.database)

    def _verify_root(self, row):
        info = self.sources.resolver.inspect(row["root_path"])
        if identity_values(info)["source_identity_key"] != row["source_identity_key"]:
            raise ScanError("SOURCE_DISCONNECTED", "The source volume identity changed or became unavailable.", row["root_path"])

    def scan(self, source_id: int, *, context=None, cancel: Event | None = None,
             progress=lambda value: None) -> dict:
        cancel = cancel or Event()
        def stopped():
            return cancel.is_set() or (context is not None and context.is_cancelled)
        def checkpoint():
            if stopped():
                raise Cancelled()
        checkpoint()
        if self.repository.active(source_id):
            raise ScanError("SCAN_ACTIVE", "This source is already being scanned.")
        row = self.sources._get(source_id)
        if not row["enabled"]:
            raise ScanError("SOURCE_DISABLED", f"Unable to start scan for {row['name']}: the source is disabled.", row["root_path"])
        row = self.sources.refresh(source_id)
        if not row["online"] or not row["normalized_root_path"] or not row["source_identity_key"]:
            raise ScanError("SOURCE_OFFLINE", f"Unable to start scan for {row['name']}: the source is offline or its root identity is unresolved. {row['status_message'] or ''}", row["root_path"])
        if self.policy.excludes(row["root_path"]):
            raise ScanError("APPLICATION_PATH_EXCLUDED", "This root is inside a protected application-owned folder.", row["root_path"])
        scan_id = self.repository.claim(row, self.policy.concurrency)
        counters = dict.fromkeys(COUNTERS, 0)
        counters["coverage_complete"] = 1
        assets, directories, issues = [], [], []
        current = row["root_path"]
        last_progress = 0.0

        def emit(force=False):
            nonlocal last_progress
            if force or monotonic() - last_progress >= 0.1:
                progress(dict(scan_id=scan_id, source_id=source_id, status="RUNNING",
                              current_folder=current, **counters))
                last_progress = monotonic()

        def flush(*, verify=True):
            if verify:
                self._verify_root(row)
            self.repository.write_chunk(scan_id, source_id, assets, directories, issues, counters, current)
            assets.clear()
            directories.clear()
            issues.clear()
            emit()

        def warn(path, error, *, error_count=True):
            counters["coverage_complete"] = 0
            counters["files_skipped"] += 1
            counters["errors"] += int(error_count)
            issues.append(issue_for(path, error))

        try:
            emit(True)
            while True:
                checkpoint()
                current = self.repository.next_directory(scan_id)
                if current is None:
                    current = row["root_path"]
                    break
                self._verify_root(row)
                try:
                    metadata = self.filesystem.directory_stat(current)
                    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        warn(current, ScanError("REPARSE_SKIPPED", "Reparse/symbolic-link directory skipped.", current), error_count=False)
                    elif not stat.S_ISDIR(metadata.st_mode):
                        warn(current, ScanError("DIRECTORY_NOT_FOUND", "Expected directory is no longer a directory.", current))
                    else:
                        counters["directories_seen"] += 1
                        with self.filesystem.entries(current) as entries:
                            for entry in entries:
                                checkpoint()
                                path = entry.path
                                try:
                                    clean = normalize_windows_path(path)
                                    if self.policy.excludes(path):
                                        warn(path, ScanError("APPLICATION_PATH_EXCLUDED", "Protected application-owned path skipped.", path), error_count=False)
                                    else:
                                        metadata = entry.stat(follow_symlinks=False)
                                        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                                            warn(path, ScanError("REPARSE_SKIPPED", "Reparse/symbolic-link entry skipped.", path), error_count=False)
                                        elif stat.S_ISDIR(metadata.st_mode):
                                            directories.append((clean.key, clean.display))
                                        elif stat.S_ISREG(metadata.st_mode):
                                            counters["files_seen"] += 1
                                            kind = media_type(entry.name)
                                            if kind is None:
                                                counters["files_skipped"] += 1
                                                counters["unsupported_files"] += 1
                                            else:
                                                counters["supported_files"] += 1
                                                relative = ntpath.relpath(clean.display, row["root_path"])
                                                relative_key = relative_path_key(relative)
                                                if relative_key is None:
                                                    raise ScanError("INVALID_PATH", "Path cannot be safely related to the registered source.", path)
                                                values = dict(absolute_path=clean.display, relative_path=relative,
                                                    filename=entry.name, extension=ntpath.splitext(entry.name)[1].casefold(),
                                                    media_type=kind, file_size=metadata.st_size,
                                                    created_time=timestamp(getattr(metadata, "st_birthtime", metadata.st_ctime)),
                                                    modified_time=timestamp(metadata.st_mtime), indexed_time=datetime.now(timezone.utc).isoformat(),
                                                    normalized_absolute_path=clean.key, normalized_relative_path=relative_key)
                                                assets.append(values)
                                                counters["bytes_seen"] += metadata.st_size
                                        else:
                                            warn(path, ScanError("SPECIAL_ENTRY_SKIPPED", "Non-regular filesystem entry skipped.", path), error_count=False)
                                except (OSError, ValueError, SourceError, ScanError) as exc:
                                    warn(path, exc)
                                if len(assets) + len(directories) + len(issues) >= self.policy.chunk_size:
                                    checkpoint()
                                    flush()
                                emit()
                except OSError as exc:
                    # A root-level failure is fatal; child failures can continue only
                    # while the original registered root/volume still verifies.
                    self._verify_root(row)
                    if normalize_windows_path(current).key == row["normalized_root_path"]:
                        raise ScanError("SOURCE_DISCONNECTED", "The registered source could not be enumerated.", current, str(exc)) from exc
                    warn(current, exc)
                checkpoint()
                flush()
            checkpoint()
            self._verify_root(row)
            flush()
            result = self.repository.finish(scan_id, "COMPLETED", counters, stopped=stopped, identity_verified=True)
        except Exception as exc:
            was_cancelled = stopped() or isinstance(exc, Cancelled) or getattr(exc, "error_code", None) == "CANCEL_REQUESTED"
            if not was_cancelled:
                logging.getLogger("media_studio.scanner").exception("Scan failed: %s", row["root_path"])
                warn(current or row["root_path"], exc)
            # Discard uncommitted discoveries on failure/cancel; previous chunks remain valid.
            assets.clear()
            directories.clear()
            flush(verify=False)
            result = self.repository.finish(scan_id, "CANCELLED" if was_cancelled else "FAILED", counters)
            if not was_cancelled and isinstance(exc, (SourceError, ScanError)) and getattr(exc, "error_code", "") in {"SOURCE_MISSING", "SOURCE_DISCONNECTED", "DEVICE_OFFLINE", "ACCESS_DENIED"}:
                self.sources.repository.update(source_id, online=False, status_message=exc.user_message)
            return result
        # The operational scan has committed. A build failure must never relabel it
        # FAILED or discard its sealed observations; retry uses only the database.
        try:
            snapshot = self.snapshots.finalize(scan_id)
            result["snapshot_id"] = snapshot["snapshot_id"]
        except Exception as exc:
            result["snapshot_error"] = str(exc)
        return result

    def latest(self, source_id):
        return self.repository.latest(source_id)

    def issues(self, scan_id, **kwargs):
        return self.repository.issues(scan_id, **kwargs)

    def result(self, scan_id):
        return self.repository.get(scan_id)
