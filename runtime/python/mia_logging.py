from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


class RedactingFilter(logging.Filter):
    patterns = (
        re.compile(r"(?i)(password|token|secret|authorization|cookie|session|credential|api[_-]?key)\s*[=:]\s*[^\s,;]+"),
        re.compile(r"\b\d{10}(?:-\d{3})?\b"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in self.patterns:
            message = pattern.sub("[REDACTED]", message)
        record.msg = message
        record.args = ()
        return True


def _configure_file_logger(name: str, filename: Path, level: int) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(level)
    handler = RotatingFileHandler(filename, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def configure_logging(log_directory: Path, level: str = "INFO") -> logging.Logger:
    log_directory.mkdir(parents=True, exist_ok=True)
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    runtime = _configure_file_logger("mia_runtime", log_directory / "runtime.log", resolved_level)
    _configure_file_logger("mia_crawler", log_directory / "crawler.log", resolved_level)
    return runtime
