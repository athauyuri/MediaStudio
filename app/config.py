"""Read-only TOML configuration. Loading never creates or rewrites files."""
from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class AppConfig:
    title: str = "Local Media Studio"
    width: int = 1100
    height: int = 720
    worker_count: int = 2
    log_max_bytes: int = 2_000_000
    log_backup_count: int = 3
    database_path: str = "data/media_studio.db"
    scan_chunk_size: int = 250
    scan_concurrency: int = 1
    scan_excluded_paths: tuple[str, ...] = ()


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    with path.open("rb") as stream:
        values = tomllib.load(stream)
    defaults = AppConfig()
    if set(values) - set(defaults.__dataclass_fields__):
        raise ValueError("Unknown configuration key")
    limits = {"width": (640, 7680), "height": (480, 4320),
              "worker_count": (1, 16), "log_max_bytes": (1024, 100_000_000),
              "log_backup_count": (1, 20), "scan_chunk_size": (1, 1000),
              "scan_concurrency": (1, 4)}
    for key, value in values.items():
        if key == "scan_excluded_paths":
            if not isinstance(value, list) or any(not isinstance(item, str) or not item or "\x00" in item for item in value):
                raise ValueError("scan_excluded_paths must be a list of non-empty paths")
            values[key] = tuple(value)
        elif key in {"title", "database_path"}:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be non-empty text")
            if "\x00" in value:
                raise ValueError(f"{key} must not contain NUL")
        elif type(value) is not int or not limits[key][0] <= value <= limits[key][1]:
            raise ValueError(f"Invalid configuration value: {key}")
    return AppConfig(**values)
