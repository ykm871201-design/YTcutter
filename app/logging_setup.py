"""파일 로그 + GUI 로그 패널 연동."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import data_dir

LOGGER_NAME = "ytcutter"


def setup_logging() -> logging.Logger:
    log = logging.getLogger(LOGGER_NAME)
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=3,
                             encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)
    return log


def log_file_path():
    return data_dir() / "logs" / "app.log"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
