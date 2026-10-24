class ScanError(Exception):
    def __init__(self, code: str, message: str, path: str = "", technical_message: str = ""):
        self.error_code = code
        self.user_message = message + " No media files were changed."
        self.source_path = path
        self.technical_message = technical_message
        super().__init__(self.user_message)
