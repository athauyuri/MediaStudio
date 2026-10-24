class SourceError(Exception):
    def __init__(self, code: str, message: str, path: str | None = None,
                 technical_message: str = "", existing: dict | None = None):
        self.error_code = code
        self.user_message = message
        self.source_path = path
        self.technical_message = technical_message
        self.existing = existing
        super().__init__(message)
