from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from .mia_license_v2 import MiaLicenseService
from .route import create_router


def create_app() -> FastAPI:
    secret = os.environ.get("MIA_LICENSE_TOKEN_SECRET", "").encode("utf-8")
    if len(secret) < 32:
        raise RuntimeError("MIA_LICENSE_TOKEN_SECRET must contain at least 32 bytes")
    database = Path(os.environ.get("MIA_LICENSE_DATABASE", "MIA/license.db"))
    service = MiaLicenseService(database, token_secret=secret)
    app = FastAPI(title="MIA License V2", docs_url=None, redoc_url=None)
    app.include_router(create_router(service))
    return app


app = create_app()
