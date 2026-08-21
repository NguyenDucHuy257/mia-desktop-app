from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


PATTERNS = (
    re.compile(
        r"(?i)(password|token|secret|authorization|cookie|session|credential|api[_-]?key)"
        r"[\"']?\s*[=:]\s*(?:[\"'][^\"']*[\"']|[^\s,;}\]]+)"
    ),
    re.compile(r"\b\d{10}(?:-\d{3})?\b"),
)


def redact_text(value: str) -> str:
    output = value
    output = PATTERNS[0].sub(lambda match: f"{match.group(1)}=[REDACTED]", output)
    output = PATTERNS[1].sub("[REDACTED]", output)
    return output


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def _file_handler(filename: Path) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        filename,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    handler.addFilter(RedactingFilter())
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    return handler


def _reset_logger(name: str, level: int, handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def configure_logging(log_directory: Path, level: str = "INFO") -> logging.Logger:
    log_directory.mkdir(parents=True, exist_ok=True)
    resolved_level = getattr(logging, level.upper(), logging.INFO)

    runtime_handler = _file_handler(log_directory / "runtime.log")
    runtime = _reset_logger("mia_runtime", resolved_level, runtime_handler)

    crawler_handler = _file_handler(log_directory / "crawler.log")
    _reset_logger("mia_crawler", resolved_level, crawler_handler)

    # Production source uses both module-name loggers (app.*) and explicit
    # mia.worker_runtime* loggers. Attach one redacted sink at their namespace
    # roots so every upstream diagnostic reaches crawler.log without modifying
    # the vendored source tree.
    _reset_logger("app", resolved_level, crawler_handler)
    _reset_logger("mia.worker_runtime", resolved_level, crawler_handler)

    return runtime
