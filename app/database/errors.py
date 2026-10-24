from pathlib import Path


class DatabaseError(Exception):
    """Structured database failure with a safe user message and technical context."""

    def __init__(self, operation: str, path: Path, cause: Exception):
        self.operation = operation
        self.path = path
        self.error_code = getattr(cause, "sqlite_errorname", "DATABASE_ERROR")
        self.technical_message = str(cause)
        self.exception_type = type(cause).__name__
        self.retryable = self.error_code in {"SQLITE_BUSY", "SQLITE_LOCKED"}
        self.user_message = (
            f"Unable to {operation} the application database at {path}. "
            "Check the configured path, file permissions, and database compatibility. "
            "No media files were changed.")
        super().__init__(f"{self.user_message} [{self.error_code}] {cause}")
