"""Lexical Windows source paths; never resolve links or access the filesystem.

This normalization policy is part of Migration 2 history. After release, preserve
these functions and introduce a versioned policy for any normalization changes.
"""
from dataclasses import dataclass
import ntpath


@dataclass(frozen=True)
class WindowsPath:
    display: str
    key: str


def normalize_windows_path(value: str) -> WindowsPath:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Choose a fully qualified Windows folder path.")
    value = value.replace("/", "\\")
    if value.startswith("\\\\.\\"):
        raise ValueError("Device paths are not supported source roots.")
    if value[:8].casefold() == "\\\\?\\unc\\":
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    drive, tail = ntpath.splitdrive(value)
    if not drive or (not tail.startswith("\\") and not drive.startswith("\\\\")):
        raise ValueError("Choose a fully qualified Windows folder path.")
    if drive.startswith("\\\\"):
        if len(drive[2:].split("\\")) != 2 or not all(drive[2:].split("\\")):
            raise ValueError("A network path must include a server and share.")
    elif len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha():
        raise ValueError("Unsupported Windows path namespace.")
    components = tail.split("\\")
    if any(part not in {"", ".", ".."} and
           (part.endswith((" ", ".")) or any(char in part for char in '<>:"|?*'))
           for part in components):
        raise ValueError("The folder path contains unsupported Windows name characters.")
    clean = ntpath.normpath(drive + (tail or "\\"))
    if not drive.startswith("\\\\"):
        clean = clean[0].upper() + clean[1:]
    return WindowsPath(clean, clean.casefold())


def migration_path_key(value: str) -> str | None:
    """Unresolvable legacy values survive Migration 2 without filesystem probing."""
    try:
        return normalize_windows_path(value).key
    except ValueError:
        return None


def contains_path(parent: str, child: str) -> bool:
    return child.startswith(parent.rstrip("\\") + "\\")


def relative_path_key(value: str) -> str | None:
    """M4 relative comparison policy, also part of Migration 3 history."""
    if not value or ntpath.isabs(value) or ntpath.splitdrive(value)[0]:
        return None
    clean = ntpath.normpath(value.replace("/", "\\"))
    if clean == ".." or clean.startswith("..\\"):
        return None
    try:
        return normalize_windows_path("Z:\\" + clean).key[3:]
    except ValueError:
        return None
