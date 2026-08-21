from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from app.external_api.repository import (
    ApiControlRepository,
    PostgreSQLApiControlRepository,
    SQLiteApiControlRepository,
)
from app.job_engine.factory import (
    CONTROL_DATABASE_URL_ENV,
    DEFAULT_SQLITE_PATH,
    _sqlite_path_from_url,
)


def create_api_control_repository(
    *, database_url: str | None = None, sqlite_path: Path | str | None = None,
) -> ApiControlRepository:
    if sqlite_path is not None and database_url is None:
        return SQLiteApiControlRepository(sqlite_path)
    url = database_url or os.getenv(CONTROL_DATABASE_URL_ENV)
    if not url:
        return SQLiteApiControlRepository(DEFAULT_SQLITE_PATH)
    scheme = urlsplit(url).scheme.casefold()
    if scheme in {'postgresql', 'postgres'}:
        return PostgreSQLApiControlRepository(url)
    if scheme == 'sqlite':
        return SQLiteApiControlRepository(_sqlite_path_from_url(url))
    raise ValueError('unsupported MIA control database URL scheme')
