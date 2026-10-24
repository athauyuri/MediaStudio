from datetime import datetime, timezone
import ntpath
from functools import wraps

from app.database.errors import DatabaseError

from app.database.repositories.sources import SourceRepository
from app.services.source_errors import SourceError
from app.services.windows_volumes import WindowsVolumeResolver, VolumeInfo
from app.utils.windows_paths import normalize_windows_path


def source_operation(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except DatabaseError as exc:
            if "SOURCE_SCAN_ACTIVE" in exc.technical_message:
                raise SourceError("SCAN_ACTIVE", "This source is being scanned. Cancel or finish the scan before changing its registration.") from exc
            if "SNAPSHOT_HISTORY_RETAINED" in exc.technical_message or "FOREIGN KEY constraint failed" in exc.technical_message:
                raise SourceError("HISTORY_RETAINED", "This registration has retained scan/snapshot history and cannot be removed. Disable it instead; no folders or files were deleted.") from exc
            raise
    return call


def identity_values(info: VolumeInfo) -> dict:
    path = normalize_windows_path(info.root_path)
    relative = (info.relative_path or "").replace("/", "\\").strip("\\")
    if info.guid and info.relative_path is not None:
        identity = "volume:" + info.guid.casefold() + "|" + relative.casefold()
    else:
        identity = "path:" + path.key
    return dict(root_path=path.display, normalized_root_path=path.key,
        current_mount=info.mount, volume_guid=info.guid, volume_serial=info.serial,
        volume_identifier=info.guid, volume_relative_path=relative if info.guid else None,
        source_identity_key=identity, online=True,
        last_seen_at=datetime.now(timezone.utc).isoformat(),
        status_message=info.warning or ("" if info.guid else "Stable volume identity unavailable; path fallback only."))


class SourceService:
    def __init__(self, repository: SourceRepository, resolver=None):
        self.repository = repository
        self.resolver = resolver if resolver is not None else WindowsVolumeResolver()

    @staticmethod
    def _path(path: str) -> str:
        try:
            return normalize_windows_path(path).display
        except ValueError as exc:
            raise SourceError("INVALID_PATH", str(exc), path) from exc

    def _get(self, source_id: int) -> dict:
        row = self.repository.get(source_id)
        if row is None:
            raise SourceError("SOURCE_MISSING", "This source registration no longer exists.")
        return row

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
            raise SourceError("INVALID_NAME", "Enter a display name between 1 and 255 characters.")
        return name.strip()

    def register(self, path: str, name: str | None = None, *, allow_nested: bool = False) -> dict:
        path = self._path(path)
        name = self._name(name if name is not None else ntpath.basename(path) or path)
        info = self.resolver.inspect(path)
        values = identity_values(info)
        source_id = self.repository.register(dict(name=name, **values), allow_nested=allow_nested)
        return self._get(source_id)

    def list_sources(self, *, after_id: int = 0, limit: int = 100) -> list[dict]:
        return self.repository.page(after_id=after_id, limit=limit)

    @source_operation
    def rename(self, source_id: int, name: str) -> dict:
        self._get(source_id)
        self.repository.update(source_id, name=self._name(name))
        return self._get(source_id)

    @source_operation
    def set_enabled(self, source_id: int, enabled: bool) -> dict:
        self._get(source_id)
        self.repository.update(source_id, enabled=bool(enabled))
        return self._get(source_id)

    @source_operation
    def remove(self, source_id: int) -> None:
        self._get(source_id)
        self.repository.remove_registration(source_id)

    @source_operation
    def refresh(self, source_id: int) -> dict:
        row = self._get(source_id)
        try:
            info = None
            if row["volume_guid"]:
                # Search only metadata-provided mounts for this exact volume.
                last_error = None
                for mount in self.resolver.mounts(row["volume_guid"]):
                    candidate = ntpath.join(mount, row["volume_relative_path"] or "")
                    try:
                        found = self.resolver.inspect(candidate)
                        if found.guid and found.guid.casefold() == row["volume_guid"].casefold() and (found.relative_path or "").casefold() == (row["volume_relative_path"] or "").casefold():
                            info = found
                            break
                        last_error = SourceError("IDENTITY_MISMATCH", "The mounted folder does not match the registered volume identity.", candidate)
                    except SourceError as exc:
                        last_error = exc
                if info is None:
                    raise last_error or SourceError("DEVICE_OFFLINE", "The expected volume is not mounted.", row["root_path"])
            else:
                info = self.resolver.inspect(self._path(row["root_path"]))
            self.repository.update_identity(source_id, identity_values(info))
        except SourceError as exc:
            # Offline/unresolved roots retain registration, identity, and all assets.
            self.repository.update(source_id, online=False, status_message=exc.user_message)
        return self._get(source_id)

    @staticmethod
    def status(row: dict) -> str:
        if not row["enabled"]:
            return "Disabled"
        if not row["online"]:
            return "Offline"
        if not row.get("volume_guid"):
            return "Identity unresolved"
        return "Online"
