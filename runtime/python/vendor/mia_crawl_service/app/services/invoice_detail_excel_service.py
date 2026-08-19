from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from app.config.crawl_config import VALID_DIRECTIONS, VALID_QUERY_TYPES
from app.exporters.invoice_detail_excel_exporter import InvoiceDetailExcelExporter
from app.repositories.invoice_detail_query_repository import InvoiceDetailQueryRepository
from app.services.invoice_detail_download_service import InvoiceDetailDownloadService

logger = logging.getLogger(__name__)

class InvoiceDetailExcelService:
    """Ensure local details are complete, then export them as an Excel artifact."""

    def __init__(
        self,
        detail_query_repository: InvoiceDetailQueryRepository,
        detail_download_service: InvoiceDetailDownloadService | None,
        excel_exporter: InvoiceDetailExcelExporter,
        headers_provider: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.detail_query_repository = detail_query_repository
        self.detail_download_service = detail_download_service
        self.excel_exporter = excel_exporter
        self.headers_provider = headers_provider

    def export_detail_excel(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        output_dir: Path,
        output_name: str,
        fetch_new_details: bool = False,
        overwrite: bool = False,
        allow_partial: bool = False,
    ) -> dict[str, Any]:
        self._validate_request(
            company_tax_code, direction, query_type, from_date, to_date,
            output_name,
        )
        output_path = Path(output_dir) / output_name
        if output_path.exists() and not overwrite:
            raise FileExistsError(f'Output already exists: {output_path}')

        completeness = self.detail_query_repository.inspect_detail_completeness(
            company_tax_code, direction, query_type, from_date, to_date
        )
        download_summary: dict[str, Any] | None = None
        if fetch_new_details:
            download_summary = self._download_details(
                company_tax_code=company_tax_code,
                direction=direction,
                query_type=query_type,
                from_date=from_date,
                to_date=to_date,
                only_pending=False,
            )
        elif completeness['invalid_count'] > 0:
            logger.warning(
                'Found %d overview items without verified detail; refreshing details',
                completeness['invalid_count'],
            )
            download_summary = self._download_details(
                company_tax_code=company_tax_code,
                direction=direction,
                query_type=query_type,
                from_date=from_date,
                to_date=to_date,
                # A corrupt/missing artifact may still have detail_fetched=1.
                # Re-evaluate this exact requested range instead of trusting it.
                only_pending=False,
            )

        completeness = self.detail_query_repository.inspect_detail_completeness(
            company_tax_code, direction, query_type, from_date, to_date
        )
        self._require_complete(completeness, allow_partial=allow_partial)

        detail_records = self.detail_query_repository.get_detail_records_for_export(
            company_tax_code, direction, query_type, from_date, to_date
        )
        if not detail_records:
            if download_summary is None and self.detail_download_service is not None:
                download_summary = self._download_details(
                    company_tax_code=company_tax_code,
                    direction=direction,
                    query_type=query_type,
                    from_date=from_date,
                    to_date=to_date,
                    only_pending=True,
                )
                detail_records = (
                    self.detail_query_repository.get_detail_records_for_export(
                        company_tax_code, direction, query_type, from_date, to_date
                    )
                )
            if not detail_records:
                raise RuntimeError(
                    'No local invoice detail data is available for the requested range'
                )

        completeness = self.detail_query_repository.inspect_detail_completeness(
            company_tax_code, direction, query_type, from_date, to_date
        )
        self._require_complete(completeness, allow_partial=allow_partial)
        if completeness['invalid_count']:
            logger.warning(
                'Partial export explicitly allowed with %d invalid detail artifacts',
                completeness['invalid_count'],
            )
        summary = self.excel_exporter.export(
            detail_records=detail_records,
            output_path=output_path,
            from_date=from_date,
            to_date=to_date,
        )
        summary.update({
            'company_tax_code': company_tax_code,
            'direction': direction,
            'query_type': query_type,
            'from_date': from_date,
            'to_date': to_date,
            'fetch_new_details': fetch_new_details,
            'detail_record_count': len(detail_records),
            'missing_detail_count': completeness['invalid_count'],
            'detail_completeness': completeness,
            'partial_export': bool(completeness['invalid_count']),
        })
        if download_summary is not None:
            summary['download_summary'] = download_summary
        return summary

    @staticmethod
    def _require_complete(
        completeness: dict[str, int], *, allow_partial: bool
    ) -> None:
        if completeness['invalid_count'] and not allow_partial:
            raise RuntimeError(
                'Invoice detail export blocked: local detail artifacts are incomplete '
                f"(missing={completeness['missing_count']}, "
                f"corrupt={completeness['corrupt_count']}, "
                f"unreadable={completeness['unreadable_count']}, "
                f"error={completeness['error_count']})"
            )

    def _download_details(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        only_pending: bool,
    ) -> dict[str, Any]:
        if self.detail_download_service is None or self.headers_provider is None:
            raise RuntimeError(
                'Detail data is missing and detail_download_service is not configured'
            )
        return self.detail_download_service.download_invoice_details(
            headers=self.headers_provider(),
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            fetch_new_overview=False,
            only_pending=only_pending,
            max_details=None,
        )

    @staticmethod
    def _validate_request(
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        output_name: str,
    ) -> None:
        if not company_tax_code.strip():
            raise ValueError('company_tax_code must not be empty')
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f'Unsupported direction: {direction!r}')
        if query_type not in VALID_QUERY_TYPES:
            raise ValueError(f'Unsupported query_type: {query_type!r}')
        try:
            parsed_from = date.fromisoformat(from_date)
            parsed_to = date.fromisoformat(to_date)
        except (TypeError, ValueError) as error:
            raise ValueError('from_date and to_date must use YYYY-MM-DD') from error
        if from_date != parsed_from.isoformat() or to_date != parsed_to.isoformat():
            raise ValueError('from_date and to_date must use YYYY-MM-DD')
        if parsed_from > parsed_to:
            raise ValueError('from_date must not be after to_date')
        if not output_name or Path(output_name).name != output_name:
            raise ValueError('output_name must be a file name without directories')
        if Path(output_name).suffix.lower() != '.xlsx':
            raise ValueError('output_name must use the .xlsx extension')
