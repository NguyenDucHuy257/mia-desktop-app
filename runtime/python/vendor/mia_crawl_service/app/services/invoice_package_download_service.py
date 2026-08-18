from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any

from app.config.crawl_config import QUERY_TYPE_TO_CATEGORY, VALID_DIRECTIONS
from app.crawlers.invoice_package_crawler import (
    InvoicePackageCrawler,
    InvoicePackageUnavailableError,
    build_invoice_package_headers,
)
from app.repositories.invoice_package_repository import InvoicePackageRepository
from app.services.invoice_package_storage_service import InvoicePackageStorageService

logger = logging.getLogger(__name__)

class InvoicePackageDownloadService:
    """Coordinate local key selection, package download, and persistence."""

    def __init__(
        self,
        package_crawler: InvoicePackageCrawler,
        package_repository: InvoicePackageRepository,
        storage_service: InvoicePackageStorageService,
        delay_between_items_seconds: float = 0.15,
    ) -> None:
        if delay_between_items_seconds < 0:
            raise ValueError('delay_between_items_seconds must not be negative')
        self.package_crawler = package_crawler
        self.package_repository = package_repository
        self.storage_service = storage_service
        self.delay_between_items_seconds = delay_between_items_seconds

    def download_invoice_packages(
        self,
        headers: dict[str, str],
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        export_xml: bool,
        export_html: bool,
        only_pending: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        invoice_category = self._validate_request(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            export_xml=export_xml,
            export_html=export_html,
            limit=limit,
        )
        requeued_missing_file_count = (
            self.package_repository.reconcile_missing_package_files(
                company_tax_code=company_tax_code,
                direction=direction,
                query_type=query_type,
                from_date=from_date,
                to_date=to_date,
                export_xml=export_xml,
                export_html=export_html,
            )
            if only_pending
            else 0
        )
        all_items = self.package_repository.get_invoice_keys_for_package_download(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            export_xml=export_xml,
            export_html=export_html,
            only_pending=False,
            limit=None,
        )
        items = self.package_repository.get_invoice_keys_for_package_download(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            export_xml=export_xml,
            export_html=export_html,
            only_pending=only_pending,
            limit=limit,
        )
        logger.info(
            'Selected %d invoice keys for package download company=%s '
            'direction=%s query_type=%s range=%s..%s',
            len(items), company_tax_code, direction, query_type, from_date, to_date,
        )

        export_kind = 'html' if export_html else 'xml'
        package_headers = build_invoice_package_headers(
            headers, direction=direction, export_kind=export_kind
        )
        downloaded_count = 0
        failed_count = 0
        for current, item in enumerate(items, start=1):
            try:
                package = self.package_crawler.download_invoice_package(
                    headers=package_headers,
                    query_type=query_type,
                    nbmst=item['nbmst'],
                    khhdon=item['khhdon'],
                    shdon=item['shdon'],
                    khmshdon=item['khmshdon'],
                )
                self.storage_service.save_invoice_package(
                    company_tax_code=company_tax_code,
                    direction=direction,
                    query_type=query_type,
                    invoice_category=invoice_category,
                    invoice_item=item,
                    zip_bytes=package,
                    from_date=from_date,
                    to_date=to_date,
                    export_xml=export_xml,
                    export_html=export_html,
                )
                downloaded_count += 1
                logger.info(
                    '[%d/%d] package downloaded nbmst=%s khhdon=%s '
                    'shdon=%s khmshdon=%s',
                    current, len(items), item['nbmst'], item['khhdon'],
                    item['shdon'], item['khmshdon'],
                )
            except Exception as error:
                is_unavailable = isinstance(error, InvoicePackageUnavailableError)
                if not is_unavailable:
                    failed_count += 1
                try:
                    self.package_repository.upsert_package_error(
                        company_tax_code=company_tax_code,
                        direction=direction,
                        query_type=query_type,
                        invoice_category=invoice_category,
                        nbmst=item['nbmst'],
                        khhdon=item['khhdon'],
                        shdon=item['shdon'],
                        khmshdon=item['khmshdon'],
                        nlap=item.get('nlap'),
                        nlap_date=item.get('nlap_date'),
                        error_message=f'{type(error).__name__}: {error}',
                        attempted_at=datetime.now(timezone.utc).isoformat(),
                        unavailable=is_unavailable,
                    )
                except Exception:
                    logger.exception(
                        '[%d/%d] could not persist package error nbmst=%s '
                        'khhdon=%s shdon=%s khmshdon=%s',
                        current, len(items), item['nbmst'], item['khhdon'],
                        item['shdon'], item['khmshdon'],
                    )
                if is_unavailable:
                    logger.warning(
                        '[%d/%d] package unavailable nbmst=%s khhdon=%s '
                        'shdon=%s khmshdon=%s reason=%s',
                        current, len(items), item['nbmst'], item['khhdon'],
                        item['shdon'], item['khmshdon'], error,
                    )
                else:
                    logger.error(
                        '[%d/%d] package failed nbmst=%s khhdon=%s shdon=%s '
                        'khmshdon=%s error=%s: %s',
                        current, len(items), item['nbmst'], item['khhdon'],
                        item['shdon'], item['khmshdon'], type(error).__name__, error,
                    )

            if current < len(items) and self.delay_between_items_seconds > 0:
                logger.debug(
                    'Waiting %.2fs before the next invoice package',
                    self.delay_between_items_seconds,
                )
                time.sleep(self.delay_between_items_seconds)

        package_dir = (
            self.storage_service.base_data_dir
            / company_tax_code
            / 'exports'
            / 'invoice_packages'
            / direction
            / query_type
            / f'{from_date}_{to_date}'
        ).resolve()
        unavailable_count = self.package_repository.count_unavailable_packages(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
        )
        verified_existing_count = (
            self.package_repository.count_verified_package_files(
                company_tax_code=company_tax_code,
                direction=direction,
                query_type=query_type,
                from_date=from_date,
                to_date=to_date,
                export_xml=export_xml,
                export_html=export_html,
            )
        )
        pending_count = len(
            self.package_repository.get_invoice_keys_for_package_download(
                company_tax_code=company_tax_code,
                direction=direction,
                query_type=query_type,
                from_date=from_date,
                to_date=to_date,
                export_xml=export_xml,
                export_html=export_html,
                only_pending=True,
                limit=None,
            )
        )
        summary = {
            'company_tax_code': company_tax_code,
            'direction': direction,
            'query_type': query_type,
            'invoice_category': invoice_category,
            'from_date': from_date,
            'to_date': to_date,
            'export_xml': export_xml,
            'export_html': export_html,
            'total_items': len(items),
            'source_invoice_count': len(all_items),
            'downloaded_count': downloaded_count,
            'verified_existing_count': verified_existing_count,
            'requeued_missing_file_count': requeued_missing_file_count,
            'pending_count': pending_count,
            'failed_count': failed_count,
            'unavailable_count': unavailable_count,
            'is_complete': failed_count == 0 and pending_count == 0,
            'skipped_count': 0,
            'xml_dir': str(package_dir / 'xml'),
            'html_dir': str(package_dir / 'html'),
            'db_path': str(self.package_repository.database_path.resolve()),
        }
        logger.info('Completed invoice package batch summary=%s', summary)
        return summary

    @staticmethod
    def _validate_request(
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        export_xml: bool,
        export_html: bool,
        limit: int | None,
    ) -> str:
        if not company_tax_code.strip():
            raise ValueError('company_tax_code must not be empty')
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f'Unsupported direction: {direction!r}')
        invoice_category = QUERY_TYPE_TO_CATEGORY.get(query_type)
        if invoice_category is None:
            raise ValueError(f'Unsupported query_type: {query_type!r}')
        if not export_xml and not export_html:
            raise ValueError('At least one of export_xml/export_html must be True')
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
        return invoice_category
