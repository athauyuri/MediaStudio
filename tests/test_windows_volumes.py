import ctypes
import stat
from types import SimpleNamespace

import pytest

from app.services.source_errors import SourceError
import app.services.windows_volumes as volumes


class FakeKernel:
    def GetVolumePathNameW(self, path, buffer, size):
        buffer.value = "E:\\"
        return True

    def GetVolumeNameForVolumeMountPointW(self, mount, buffer, size):
        buffer.value = "\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\"
        return True

    def GetVolumeInformationW(self, mount, label, label_size, serial, *args):
        serial._obj.value = 123
        return True

    def GetVolumePathNamesForVolumeNameW(self, guid, buffer, size, needed):
        buffer.value = "F:\\"
        needed._obj.value = 5
        return True


@pytest.fixture
def resolver(monkeypatch):
    resolver = volumes.WindowsVolumeResolver()
    resolver._api = FakeKernel()
    monkeypatch.setattr(volumes, "os", SimpleNamespace(name="nt", stat=lambda *args, **kwargs:
        SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0)))
    return resolver


def test_windows_api_metadata_and_mount_lookup(resolver):
    info = resolver.inspect("E:\\Media")
    assert info.relative_path == "Media"
    assert info.serial == "0000007B"
    assert info.guid.startswith("\\\\?\\Volume{")
    assert resolver.mounts(info.guid) == ["F:\\"]


def test_volume_root_relative_path_is_empty(resolver):
    assert resolver.inspect("E:\\").relative_path == ""


def test_unsupported_guid_has_explicit_fallback(resolver):
    ctypes.set_last_error(50)
    resolver._api.GetVolumeNameForVolumeMountPointW = lambda *args: False
    info = resolver.inspect("E:\\Media")
    assert info.guid is None and "fallback" in info.warning


def test_volume_api_access_denied_is_structured(resolver):
    ctypes.set_last_error(5)
    resolver._api.GetVolumePathNameW = lambda *args: False
    with pytest.raises(SourceError) as error:
        resolver.inspect("E:\\Media")
    assert error.value.error_code == "ACCESS_DENIED"
    ctypes.set_last_error(0)


@pytest.mark.parametrize("exception,code", [(FileNotFoundError(), "SOURCE_MISSING"),
    (PermissionError(), "ACCESS_DENIED"), (OSError(), "DEVICE_OFFLINE")])
def test_root_stat_errors(monkeypatch, resolver, exception, code):
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr(volumes.os, "stat", fail)
    with pytest.raises(SourceError) as error:
        resolver.inspect("E:\\Media")
    assert error.value.error_code == code


def test_reparse_root_rejected_without_traversal(monkeypatch, resolver):
    monkeypatch.setattr(volumes.os, "stat", lambda *args, **kwargs:
        SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT))
    with pytest.raises(SourceError) as error:
        resolver.inspect("E:\\Junction")
    assert error.value.error_code == "REPARSE_ROOT"


def test_regular_file_is_not_a_source(monkeypatch, resolver):
    monkeypatch.setattr(volumes.os, "stat", lambda *args, **kwargs:
        SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0))
    with pytest.raises(SourceError) as error:
        resolver.inspect("E:\\image.png")
    assert error.value.error_code == "NOT_DIRECTORY"


def test_mount_api_handles_disconnected_volume(resolver):
    ctypes.set_last_error(21)
    resolver._api.GetVolumePathNamesForVolumeNameW = lambda *args: False
    assert resolver.mounts("guid") == []
    ctypes.set_last_error(0)
