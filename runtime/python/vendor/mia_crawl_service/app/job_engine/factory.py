from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.job_engine.admission_safe import SafeImmediateAdmissionJobRepository
from app.job_engine.contracts import JobEngineRepository
from app.job_engine.postgres_repository import PostgreSQLJobEngineRepository
from app.job_engine.repository import SQLiteJobEngineRepository


CONTROL_DATABASE_URL_ENV = 'MIA_CONTROL_DATABASE_URL'
DEFAULT_SQLITE_PATH = Path('data/control/control.sqlite3')


def create_job_engine_repository(
    *,
    database_url: str | None = None,
    sqlite_path: Path | str | None = None,
) -> JobEngineRepository:
    if sqlite_path is not None and database_url is None:
        return SafeImmediateAdmissionJobRepository(
            SQLiteJobEngineRepository(sqlite_path)
        )
    url = database_url or os.getenv(CONTROL_DATABASE_URL_ENV)
    if url:
        scheme = urlsplit(url).scheme.casefold()
        if scheme in {'postgresql', 'postgres'}:
            return SafeImmediateAdmissionJobRepository(
                PostgreSQLJobEngineRepository(url)
            )
        if scheme == 'sqlite':
            return SafeImmediateAdmissionJobRepository(
                SQLiteJobEngineRepository(_sqlite_path_from_url(url))
            )
        raise ValueError('unsupported MIA control database URL scheme')
    return SafeImmediateAdmissionJobRepository(
        SQLiteJobEngineRepository(DEFAULT_SQLITE_PATH)
    )


def _sqlite_path_from_url(url: str) -> Path:
    parsed = urlsplit(url)
    if parsed.netloc not in {'', 'localhost'}:
        raise ValueError('sqlite URL must reference a local path')
    raw_path = unquote(parsed.path)
    if re.match(r'^/[A-Za-z]:/', raw_path):
        raw_path = raw_path[1:]
    if not raw_path:
        raise ValueError('sqlite URL must include a database path')
    return Path(raw_path)
