"""Root-only filesystem and Windows volume API boundary. No child enumeration."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import ntpath
import os
import stat

from app.services.source_errors import SourceError
from app.utils.windows_paths import normalize_windows_path


@dataclass(frozen=True)
class VolumeInfo:
    root_path: str
    mount: str | None = None
    guid: str | None = None
    serial: str | None = None
    relative_path: str | None = None
    warning: str = ""


def root_error(exc: OSError, path: str) -> SourceError:
    if isinstance(exc, PermissionError):
        return SourceError("ACCESS_DENIED", "Access to this folder is denied.", path, str(exc))
    if isinstance(exc, FileNotFoundError):
        return SourceError("SOURCE_MISSING", "Selected folder does not exist or the drive is unavailable.", path, str(exc))
    return SourceError("DEVICE_OFFLINE", "The folder or drive is currently unavailable.", path, str(exc))


class WindowsVolumeResolver:
    def __init__(self):
        # Lazy creation also makes importing safe on non-Windows test hosts.
        self._api = None

    def _kernel(self):
        if os.name != "nt":
            raise SourceError("UNSUPPORTED_PLATFORM", "Source registration requires Windows.")
        if self._api is None:
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            for name in ("GetVolumePathNameW", "GetVolumeNameForVolumeMountPointW"):
                function = getattr(api, name)
                function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
                function.restype = wintypes.BOOL
            api.GetVolumePathNamesForVolumeNameW.argtypes = [
                wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            api.GetVolumePathNamesForVolumeNameW.restype = wintypes.BOOL
            api.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR,
                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
            api.GetVolumeInformationW.restype = wintypes.BOOL
            self._api = api
        return self._api

    def inspect(self, path: str) -> VolumeInfo:
        path = normalize_windows_path(path).display
        try:
            metadata = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise root_error(exc, path) from exc
        if getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise SourceError("REPARSE_ROOT", "This root is a junction or reparse point. Choose its target folder explicitly.", path)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SourceError("NOT_DIRECTORY", "Select a folder, not a file.", path)
        api = self._kernel()
        mount = ctypes.create_unicode_buffer(32768)
        if not api.GetVolumePathNameW(path, mount, len(mount)):
            if ctypes.get_last_error() == 5:
                raise SourceError("ACCESS_DENIED", "Access to volume metadata is denied.", path)
            return VolumeInfo(path, warning="Unable to determine stable volume identity; path fallback only.")
        guid = ctypes.create_unicode_buffer(64)
        if not api.GetVolumeNameForVolumeMountPointW(mount.value, guid, len(guid)):
            if ctypes.get_last_error() == 5:
                raise SourceError("ACCESS_DENIED", "Access to volume metadata is denied.", path)
            return VolumeInfo(path, mount.value, warning="Stable volume GUID unavailable; path fallback only.")
        try:
            relative = ntpath.relpath(path, mount.value)
        except ValueError:
            return VolumeInfo(path, mount.value, warning="Root alias cannot be safely related to the volume; path fallback only.")
        if relative == ".." or relative.startswith("..\\"):
            return VolumeInfo(path, mount.value, warning="Root alias cannot be safely related to the volume; path fallback only.")
        serial = wintypes.DWORD()
        serial_text = None
        if api.GetVolumeInformationW(mount.value, None, 0, ctypes.byref(serial), None, None, None, 0):
            serial_text = f"{serial.value:08X}"
        return VolumeInfo(path, mount.value, guid.value, serial_text,
                          "" if relative == "." else relative)

    def mounts(self, guid: str) -> list[str]:
        """Query mount-manager metadata for one known GUID; never enumerate drives/files."""
        api = self._kernel()
        needed = wintypes.DWORD()
        buffer = ctypes.create_unicode_buffer(1024)
        if not api.GetVolumePathNamesForVolumeNameW(guid, buffer, len(buffer), ctypes.byref(needed)):
            error = ctypes.get_last_error()
            if error == 234 and 0 < needed.value <= 32768:
                buffer = ctypes.create_unicode_buffer(needed.value)
                if not api.GetVolumePathNamesForVolumeNameW(guid, buffer, len(buffer), ctypes.byref(needed)):
                    raise root_error(ctypes.WinError(ctypes.get_last_error()), guid)
            elif error in {2, 3, 21, 1167}:
                return []
            else:
                raise root_error(ctypes.WinError(error), guid)
        return sorted((path for path in buffer[:].split("\x00") if path), key=str.casefold)
