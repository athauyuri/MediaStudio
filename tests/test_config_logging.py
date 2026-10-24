from dataclasses import replace
import logging

import pytest

from app.config import AppConfig, load_config
from app.utils.logging import close_logging, configure_logging


def test_missing_config_is_read_only(tmp_path):
    assert load_config(tmp_path / "missing.toml") == AppConfig()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", ['""', '3', 'true', '"\\u0000"'])
def test_database_path_config_rejects_invalid_values(tmp_path, value):
    path = tmp_path / "invalid.toml"
    path.write_text("database_path = " + value)
    with pytest.raises(ValueError):
        load_config(path)


def test_database_path_config_accepts_windows_and_unicode(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("database_path = 'D:\\应用 data\\媒体.db'", encoding="utf-8")
    assert load_config(path).database_path == "D:\\应用 data\\媒体.db"


def test_scan_configuration(tmp_path):
    path = tmp_path / "scanner.toml"
    path.write_text("scan_chunk_size = 32\nscan_concurrency = 2\nscan_excluded_paths = ['cache', 'D:\\Private']")
    config = load_config(path)
    assert config.scan_chunk_size == 32 and config.scan_concurrency == 2
    assert config.scan_excluded_paths == ("cache", "D:\\Private")


@pytest.mark.parametrize("content", ["scan_chunk_size=0", "scan_concurrency=5",
    "scan_excluded_paths='bad'", "scan_excluded_paths=[3]"])
def test_invalid_scan_configuration(tmp_path, content):
    path = tmp_path / "scanner.toml"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_config(path)


def test_config_override_and_preservation(tmp_path):
    path = tmp_path / "配置.toml"
    content = b'width = 900\nworker_count = 1\n'
    path.write_bytes(content)
    assert load_config(path).width == 900
    assert path.read_bytes() == content


@pytest.mark.parametrize("content", ['worker_count = 0', 'width = true',
    'height = "large"', 'unknown = 1', 'title = " "', 'invalid TOML'])
def test_bad_config_is_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_config(path)


def test_logging_rotates_and_reinitializes(tmp_path):
    config = replace(AppConfig(), log_max_bytes=1024, log_backup_count=2)
    try:
        logger = configure_logging(tmp_path, config)
        logger = configure_logging(tmp_path, config)
        assert len(logger.handlers) == 1
        for _ in range(60):
            logger.info("rotation test %s", "x" * 100)
        assert (tmp_path / "media-studio.log.1").exists()
        assert len(list(tmp_path.iterdir())) <= 3
    finally:
        close_logging()
    assert not logging.getLogger("media_studio").handlers
