import ntpath

import pytest

from app.database.connection import Database
from app.database.repositories.sources import SourceRepository
from app.services.source_errors import SourceError
from app.services.sources import SourceService
from app.services.windows_volumes import VolumeInfo
from app.utils.windows_paths import normalize_windows_path


class FakeVolumeResolver:
    GUID = "\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\"

    def __init__(self):
        self.roots = {}
        self.mount_map = {}
        self.calls = []

    def add(self, path, guid=GUID):
        clean = normalize_windows_path(path)
        drive, tail = ntpath.splitdrive(clean.display)
        mount = drive + "\\"
        info = VolumeInfo(clean.display, mount, guid, "12345678", tail.strip("\\") if guid else None)
        self.roots[clean.key] = info
        if guid:
            self.mount_map[guid.casefold()] = [mount]
        return info

    def inspect(self, path):
        self.calls.append(("inspect", path))
        result = self.roots.get(normalize_windows_path(path).key)
        if result is None:
            raise SourceError("SOURCE_MISSING", "Selected folder does not exist.", path)
        if isinstance(result, Exception):
            raise result
        return result

    def mounts(self, guid):
        self.calls.append(("mounts", guid))
        return self.mount_map.get(guid.casefold(), [])


@pytest.fixture
def source_setup(tmp_path):
    database = Database(tmp_path / "owned" / "studio.db")
    database.initialize()
    resolver = FakeVolumeResolver()
    repository = SourceRepository(database)
    yield SourceService(repository, resolver), resolver, database
    database.close()
