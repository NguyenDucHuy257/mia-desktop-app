from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, ContextManager

from app.config.crawl_config import VALID_DIRECTIONS, VALID_QUERY_TYPES
from app.exporters.invoice_pdf_renderer import InvoicePdfRenderer
from app.repositories.invoice_package_repository import InvoicePackageRepository

logger = logging.getLogger(__name__)

class InvoicePdfExportService:
    """Convert locally stored invoice HTML files into A4 PDF files."""

    def __init__(
        self,
        base_data_dir: Path | str,
        package_repository: InvoicePackageRepository,
        renderer_factory: Callable[[], ContextManager[Any]] | None = None,
    ) -> None:
        self.base_data_dir = Path(base_data_dir)
        self.package_repository = package_repository
        # Injectable for tests; the default shares one headless Chromium
        # across the whole batch, which is what makes bulk conversion fast.
        self.renderer_factory = renderer_factory or InvoicePdfRenderer

    def export_invoice_pdfs(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        *,
        output_dir: Path | str | None = None,
        overwrite: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        self._validate_request(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
        items = self.package_repository.get_html_items_for_pdf_export(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
        resolved_output_dir = Path(
            output_dir
            if output_dir is not None
            else self.base_data_dir
            / company_tax_code
            / 'exports'
            / 'invoice_pdfs'
            / direction
            / query_type
            / f'{from_date}_{to_date}'
        ).resolve()
        logger.info(
            'Selected %d invoice HTML files for PDF export company=%s '
            'direction=%s query_type=%s range=%s..%s output=%s',
            len(items), company_tax_code, direction, query_type,
            from_date, to_date, resolved_output_dir,
        )

        converted_count = 0
        skipped_existing_count = 0
        missing_html_count = 0
        failed_count = 0
        single_page_count = 0
        multi_page_count = 0
        with self.renderer_factory() as renderer:
            for current, item in enumerate(items, start=1):
                html_path = Path(item['html_path'])
                if not html_path.is_file():
                    missing_html_count += 1
                    logger.warning(
                        '[%d/%d] invoice HTML is missing on disk path=%s',
                        current, len(items), html_path,
                    )
                    continue
                pdf_path = resolved_output_dir / f'{html_path.stem}.pdf'
                if pdf_path.is_file() and not overwrite:
                    skipped_existing_count += 1
                    continue
                try:
                    result = renderer.render_pdf(html_path, pdf_path)
                except Exception as error:
                    failed_count += 1
                    logger.error(
                        '[%d/%d] PDF conversion failed html=%s error=%s: %s',
                        current, len(items), html_path.name,
                        type(error).__name__, error,
                    )
                    continue
                converted_count += 1
                if result.get('single_page_target'):
                    single_page_count += 1
                else:
                    multi_page_count += 1
                logger.info(
                    '[%d/%d] PDF converted name=%s single_page=%s scale=%s',
                    current, len(items), pdf_path.name,
                    result.get('single_page_target'), result.get('scale'),
                )

        summary = {
            'company_tax_code': company_tax_code,
            'direction': direction,
            'query_type': query_type,
            'from_date': from_date,
            'to_date': to_date,
            'total_items': len(items),
            'converted_count': converted_count,
            'skipped_existing_count': skipped_existing_count,
            'missing_html_count': missing_html_count,
            'failed_count': failed_count,
            'single_page_count': single_page_count,
            'multi_page_count': multi_page_count,
            'is_complete': failed_count == 0 and missing_html_count == 0,
            'output_dir': str(resolved_output_dir),
            'db_path': str(self.package_repository.database_path.resolve()),
        }
        logger.info('Completed invoice PDF export summary=%s', summary)
        return summary

    @staticmethod
    def _validate_request(
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        limit: int | None,
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
        if limit is not None and limit <= 0:
            raise ValueError('limit must be greater than zero')
