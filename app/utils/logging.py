"""Application-owned rotating log handler; leaves other loggers untouched."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import AppConfig


def configure_logging(directory: Path, config: AppConfig) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("media_studio")
    close_logging()
    handler = RotatingFileHandler(directory / "media-studio.log",
                                 maxBytes=config.log_max_bytes,
                                 backupCount=config.log_backup_count, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def close_logging() -> None:
    logger = logging.getLogger("media_studio")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
