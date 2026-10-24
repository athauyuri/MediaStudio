"""Extension-only classification. No media header/content access."""
import ntpath

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm"})
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def media_type(filename: str) -> str | None:
    extension = ntpath.splitext(filename)[1].casefold()
    return "image" if extension in IMAGE_EXTENSIONS else "video" if extension in VIDEO_EXTENSIONS else None
