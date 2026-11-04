"""Rotating log file plus console output."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from open_antenna_validation.config import LoggingConfig, resolve_path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(home: Path, config: LoggingConfig) -> Path:
    path = resolve_path(home, config.file)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(config.level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=config.max_bytes, backupCount=config.backup_count, encoding="utf-8"
    )
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
    return path
