import logging

from app.database.repositories.snapshots import SnapshotRepository
from app.database.repositories.scans import ScanRepository
from app.services.process_ownership import ProcessProbe


class SnapshotService:
    def __init__(self, database, process_probe=None):
        self.repository = SnapshotRepository(database)
        self.scans = ScanRepository(database)
        self.process_probe = process_probe or ProcessProbe()

    def finalize(self, scan_id):
        try:
            return self.repository.finalize(scan_id)
        except Exception as exc:
            logging.getLogger("media_studio.snapshots").exception("Snapshot build failed for scan %s", scan_id)
            self.repository.record_error(scan_id, str(exc))
            raise

    def recover_interrupted(self):
        return self.repository.recover_interrupted(self.process_probe)

    def history(self, source_id, **kwargs):
        self.recover_interrupted()
        return self.repository.history(source_id, **kwargs)

    def summary(self, snapshot_id):
        return self.repository.get(snapshot_id)

    def manifest(self, snapshot_id, **kwargs):
        return self.repository.manifest(snapshot_id, **kwargs)

    def folders(self, snapshot_id, **kwargs):
        return self.repository.folders(snapshot_id, **kwargs)

    def issues(self, scan_id, **kwargs):
        return self.scans.issues(scan_id, **kwargs)
