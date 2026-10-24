"""Entry point: startup configuration/logging precede the Qt event loop."""
import argparse
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from app.config import load_config
from app.database.connection import Database
from app.database.errors import DatabaseError
from app.database.repositories.sources import SourceRepository
from app.services.sources import SourceService
from app.database.repositories.scans import ScanRepository
from app.services.scanner import ScanService
from app.services.scan_policy import ScanPolicy
from app.services.snapshots import SnapshotService
from app.ui.main_window import MainWindow
from app.utils.logging import close_logging, configure_logging
from app.workers.manager import WorkerManager


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Media Studio")
    parser.add_argument("--config", type=Path, help="Read-only TOML configuration path")
    parser.add_argument("--runtime-root", type=Path, default=Path.cwd(),
                        help="Application-owned runtime root for logs and relative database paths")
    args = parser.parse_args(argv)
    config_path = args.config or Path(__file__).resolve().parents[1] / "config" / "default.toml"
    try:
        config = load_config(config_path)
        logger = configure_logging(args.runtime_root.resolve() / "logs", config)
    except (OSError, ValueError) as exc:
        print(f"Media Studio startup failed: {exc}", file=sys.stderr)
        close_logging()
        return 1
    database_path = Path(config.database_path)
    if not database_path.is_absolute():
        database_path = args.runtime_root.resolve() / database_path
    database = Database(database_path)
    try:
        database.initialize()
        snapshots = SnapshotService(database)
        recovered = snapshots.recover_interrupted()
        if recovered:
            logger.info("Classified %s abandoned scans as INTERRUPTED; no source traversal resumed", recovered)
        logger.info("Database initialized: %s", database.path)
    except DatabaseError as exc:
        logger.exception("Database startup failed: %s", exc)
        print(f"Media Studio startup failed: {exc.user_message}", file=sys.stderr)
        database.close()
        close_logging()
        return 1
    workers = None
    try:
        qt_app = QApplication(sys.argv[:1])
        qt_app.setApplicationName("Local Media Studio")
        workers = WorkerManager(config.worker_count)
        sources = SourceService(SourceRepository(database))
        project_root = Path(__file__).resolve().parents[1]
        runtime_root = args.runtime_root.absolute()
        protected = [project_root, database.path.parent, config_path.absolute().parent]
        protected.extend(runtime_root / name for name in ("data", "cache", "logs", "config"))
        protected.extend(Path(path) if Path(path).is_absolute() else runtime_root / path
                         for path in config.scan_excluded_paths)
        policy = ScanPolicy(tuple(str(path) for path in protected), config.scan_chunk_size, config.scan_concurrency)
        scanner = ScanService(ScanRepository(database), sources, policy)
        window = MainWindow(config, workers, sources, scanner, snapshots)
        logger.info("Application starting")
        window.show()
        return qt_app.exec()
    finally:
        if workers is not None:
            workers.shutdown()
        database.close()
        logger.info("Application stopped")
        close_logging()


if __name__ == "__main__":
    raise SystemExit(main())
