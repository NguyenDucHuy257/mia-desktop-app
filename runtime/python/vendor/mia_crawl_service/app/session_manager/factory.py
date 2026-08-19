from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from app.job_engine.factory import (
    CONTROL_DATABASE_URL_ENV,
    DEFAULT_SQLITE_PATH,
    _sqlite_path_from_url,
)
from app.session_manager.contracts import SessionRepository
from app.session_manager.postgres_repository import PostgreSQLSessionRepository
from app.session_manager.repository import SQLiteSessionRepository


def create_session_repository(
    *,
    database_url: str | None = None,
    sqlite_path: Path | str | None = None,
) -> SessionRepository:
    if sqlite_path is not None and database_url is None:
        return SQLiteSessionRepository(sqlite_path)
    url = database_url or os.getenv(CONTROL_DATABASE_URL_ENV)
    if url:
        scheme = urlsplit(url).scheme.casefold()
        if scheme in {'postgresql', 'postgres'}:
            return PostgreSQLSessionRepository(url)
        if scheme == 'sqlite':
            return SQLiteSessionRepository(_sqlite_path_from_url(url))
        raise ValueError('unsupported MIA control database URL scheme')
    return SQLiteSessionRepository(DEFAULT_SQLITE_PATH)

