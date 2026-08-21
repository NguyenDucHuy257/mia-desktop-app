"""Compatibility name for the source-of-truth crawl runtime.

The crawler, account/session state, job admission, recovery, cache/coverage,
progress and result persistence live in the vendored mia-crawl-service modules.
This file keeps the historical ``ProductionBackend`` import while applying only
JSON-RPC/desktop presentation concerns around that source backend.
"""

from __future__ import annotations

import threading

from app.external_api.app import _job_status

from mia_source_backend import SourceBackend


class ProductionBackend(SourceBackend):
    """Thin desktop transport over SourceBackend; no crawl policy lives here."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Display-name metadata is desktop-only and its helpers can be nested
        # (_save -> _load/_write). RLock prevents a self-deadlock without
        # changing any source crawler/session/job behavior.
        self._metadata_lock = threading.RLock()

    @staticmethod
    def public_job(job):
        # Status/error/progress/current-month serialization comes directly from
        # the pinned source external API.  SourceBackend only contributes the
        # desktop transport envelope (connection/intent/source message).
        value = SourceBackend.public_job(job)
        value.update(_job_status(job).model_dump(mode="json"))
        return value


__all__ = ["ProductionBackend", "SourceBackend"]
