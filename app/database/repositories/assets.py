from app.database.repositories.base import RecordRepository
from app.utils.windows_paths import migration_path_key, relative_path_key


class AssetRepository(RecordRepository):
    table = "assets"
    id_column = "asset_id"
    required = frozenset({"source_id", "absolute_path", "relative_path", "filename",
                          "extension", "media_type"})
    writable = required | frozenset({
        "file_size", "created_time", "modified_time", "indexed_time", "width", "height",
        "aspect_ratio", "duration", "fps", "video_codec", "audio_codec", "quick_hash",
        "content_hash", "thumbnail_path", "poster_path", "preview_proxy_path",
        "rating", "favorite", "missing", "managed", "created_by_comfyui"})

    def _prepare(self, values, connection):
        # Preserve support for real pre-M4 databases used by upgrade tests/tools.
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone():
            values = dict(values)
            if "absolute_path" in values:
                values["normalized_absolute_path"] = migration_path_key(values["absolute_path"])
            if "relative_path" in values:
                values["normalized_relative_path"] = relative_path_key(values["relative_path"])
        return values
