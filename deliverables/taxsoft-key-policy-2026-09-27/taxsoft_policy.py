"""Runtime configuration for TAXSOFT/GSOFT license policies.

Import this module once from app.py after importing auth. It deliberately
changes only documented policy constants; verification and migration remain in
the existing auth.py implementation.
"""

from __future__ import annotations

import os
from datetime import datetime

import auth


def _date(name: str, default: str) -> str:
    value = str(os.getenv(name, default) or "").strip()
    datetime.strptime(value, "%Y-%m-%d")
    return value


def _version(name: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    value = str(os.getenv(name, ".".join(map(str, default))) or "").strip()
    parts = tuple(int(part) for part in value.split("."))
    if len(parts) != 3 or any(part < 0 for part in parts):
        raise RuntimeError(f"Invalid {name}")
    return parts


def configure() -> None:
    start = _date("GSOFT_TEST_DATE_FROM", auth.TEST_DATE_FROM)
    end = _date("GSOFT_TEST_DATE_TO", auth.TEST_DATE_TO)
    if start > end:
        raise RuntimeError("GSOFT_TEST_DATE_FROM must not be after GSOFT_TEST_DATE_TO")
    auth.TEST_DATE_FROM = start
    auth.TEST_DATE_TO = end
    auth.MIN_LIMITED_GSOFT_VERSION = _version(
        "GSOFT_MIN_LIMITED_VERSION", auth.MIN_LIMITED_GSOFT_VERSION
    )


configure()
