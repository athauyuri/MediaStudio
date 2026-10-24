import tracemalloc
from contextlib import contextmanager

from test_scanner import scan_setup
from app.database.repositories.scans import COUNTERS


def seed_large_observations(scanner, source, count=12000):
    scan_id = scanner.repository.claim(source, 1)
    with scanner.repository.database.transaction() as connection:
        connection.execute("""WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<?)
            INSERT INTO scan_observations(scan_id,absolute_path_at_scan,relative_path_at_scan,filename,
                extension,media_type,file_size,created_time,modified_time,comparison_key,folder_key,folder_path,observation_status)
            SELECT ?,? || '\\' || printf('folder%02d\\file%06d',x%50,x) || CASE WHEN x%2=0 THEN '.png' ELSE '.mp4' END,
                printf('folder%02d\\file%06d',x%50,x),printf('file%06d',x),
                CASE WHEN x%2=0 THEN '.png' ELSE '.mp4' END,
                CASE WHEN x%2=0 THEN 'image' ELSE 'video' END,x,'2026-10-24T01:00:00Z','2026-10-24T01:00:00Z',
                printf('folder%02d\\file%06d',x%50,x),printf('folder%02d',x%50),printf('folder%02d',x%50),'OBSERVED' FROM n""",
                (count, scan_id, source["root_path"]))
        counters = dict.fromkeys(COUNTERS, 0)
        counters.update(coverage_complete=1, directories_seen=51, files_seen=count,
                        supported_files=count, bytes_seen=count*(count+1)//2)
        fields = ",".join(key+"=?" for key in COUNTERS)
        connection.execute(f"UPDATE scan_sessions SET {fields} WHERE scan_id=?", (*[counters[key] for key in COUNTERS], scan_id))
    scanner.repository.finish(scan_id, "COMPLETED", counters, identity_verified=True)
    return scan_id


def test_database_side_large_manifest_build_paging_and_folder_aggregation(scan_setup, monkeypatch):
    scanner, _, source, _, database = scan_setup
    scan_id = seed_large_observations(scanner, source)
    statements = []
    original = database._session
    @contextmanager
    def traced(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            connection.set_trace_callback(statements.append)
            yield connection
    monkeypatch.setattr(database, "_session", traced)
    tracemalloc.start()
    try:
        summary = scanner.snapshots.finalize(scan_id)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert summary["supported_media_count"] == 12000
    assert summary["image_count"] == summary["video_count"] == 6000
    assert summary["total_bytes"] == 12000*12001//2
    assert peak < 8_000_000
    assert any("INSERT INTO scan_manifest_entries" in sql and "SELECT" in sql for sql in statements)
    first = scanner.snapshots.manifest(summary["snapshot_id"], limit=37)
    second = scanner.snapshots.manifest(summary["snapshot_id"], after_id=first[-1]["manifest_entry_id"], limit=37)
    assert len(first) == len(second) == 37
    assert first[-1]["manifest_entry_id"] < second[0]["manifest_entry_id"]
    folders = scanner.snapshots.folders(summary["snapshot_id"], limit=7)
    more = scanner.snapshots.folders(summary["snapshot_id"], after_key=folders[-1]["folder_key"], limit=7)
    assert len(folders) == len(more) == 7
    assert folders[-1]["folder_key"] < more[0]["folder_key"]
    all_folders = scanner.snapshots.folders(summary["snapshot_id"])
    assert len(all_folders) == 50
    assert sum(row["supported_media_count"] for row in all_folders) == 12000
    assert sum(row["total_bytes"] for row in all_folders) == summary["total_bytes"]
