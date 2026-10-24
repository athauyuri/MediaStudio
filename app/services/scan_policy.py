from dataclasses import dataclass

from app.utils.windows_paths import contains_path, normalize_windows_path


@dataclass(frozen=True)
class ScanPolicy:
    excluded_paths: tuple[str, ...] = ()
    chunk_size: int = 250
    concurrency: int = 1

    def __post_init__(self):
        if not 1 <= self.chunk_size <= 1000 or not 1 <= self.concurrency <= 4:
            raise ValueError("Invalid scan chunk size/concurrency")
        object.__setattr__(self, "excluded_paths", tuple(
            normalize_windows_path(path).key for path in self.excluded_paths))

    def excludes(self, path: str) -> bool:
        key = normalize_windows_path(path).key
        return any(key == root or contains_path(root, key) for root in self.excluded_paths)
