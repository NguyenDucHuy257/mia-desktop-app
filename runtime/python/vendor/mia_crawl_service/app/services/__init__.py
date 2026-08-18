"""Service package exports, loaded lazily to keep optional ML dependencies isolated."""

from __future__ import annotations

from typing import Any

__all__ = ['DownloadReport', 'OverviewDownloader', 'TaxPortalSession']


def __getattr__(name: str) -> Any:
    if name in {'DownloadReport', 'OverviewDownloader'}:
        from app.services.overview_downloader import DownloadReport, OverviewDownloader

        return {
            'DownloadReport': DownloadReport,
            'OverviewDownloader': OverviewDownloader,
        }[name]
    if name == 'TaxPortalSession':
        from app.services.portal_session import TaxPortalSession

        return TaxPortalSession
    raise AttributeError(name)
